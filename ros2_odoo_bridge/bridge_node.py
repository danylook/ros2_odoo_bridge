"""
bridge_node: nodo ROS2 Jazzy que conecta topics con Odoo 18.

Topics suscritos:
  /robot/job_completed   (std_msgs/String)  → "production_id"
  /robot/qty_produced    (std_msgs/String)  → "production_id:qty"
  /robot/job_error       (std_msgs/String)  → "production_id:mensaje"
  /robot/workorder_done  (std_msgs/String)  → "workorder_id"
  /robot/piece_placed    (std_msgs/String)  → "component_id:production_id"
  /robot/piece_moving    (std_msgs/String)  → "component_id:production_id"

Topics publicados:
  /robot/start_job       (std_msgs/String)  → JSON con datos de la orden
"""

import json
import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from std_msgs.msg import String

from .odoo_client import OdooClient, OdooAuthError, OdooCallError
from .job_server import JobServer
from .cumotion_client import CumotionClient
from .master_controller import MasterController, RobotConfig, RobotRole


class BridgeNode(Node):
    def __init__(self):
        super().__init__('odoo_bridge')

        # --- parámetros ---
        self.declare_parameter('odoo_url',      'https://odoo.tudominio.com')
        self.declare_parameter('odoo_db',       'produccion')
        self.declare_parameter('odoo_user',     'robot@tudominio.com')
        self.declare_parameter('odoo_password', '')
        self.declare_parameter('ssl_cert',      '/etc/ssl/ros2pc/cert.pem')
        self.declare_parameter('ssl_key',      '/etc/ssl/ros2pc/key.pem')
        self.declare_parameter('server_host',   '0.0.0.0')
        self.declare_parameter('server_port',   8000)
        self.declare_parameter('simulation',    True)  # True=simular, False=robot real

        p = lambda name: self.get_parameter(name).value  # noqa

        # --- cliente Odoo ---
        try:
            self.odoo = OdooClient(
                url=p('odoo_url'),
                db=p('odoo_db'),
                user=p('odoo_user'),
                password=p('odoo_password'),
            )
        except OdooAuthError as e:
            self.get_logger().fatal(f"No se pudo autenticar con Odoo: {e}")
            raise SystemExit(1)

        # --- publisher hacia los nodos del robot ---
        self.job_pub = self.create_publisher(String, '/robot/start_job', 10)

        # --- suscripciones desde los nodos del robot ---
        self.create_subscription(
            String, '/robot/job_completed', self._on_job_completed, 10)
        self.create_subscription(
            String, '/robot/qty_produced', self._on_qty_produced, 10)
        self.create_subscription(
            String, '/robot/job_error', self._on_job_error, 10)
        self.create_subscription(
            String, '/robot/workorder_done', self._on_workorder_done, 10)
        self.create_subscription(
            String, '/robot/piece_placed', self._on_piece_placed, 10)
        self.create_subscription(
            String, '/robot/piece_moving', self._on_piece_moving, 10)

        # --- master controller (simulación o real) ---
        self._simulation = p('simulation')
        self._master = MasterController(odoo_client=self.odoo,
                                        simulation=self._simulation)

        # Registrar robot de ensamblaje
        assembly_robot = RobotConfig(
            name="kuka_assembly",
            role=RobotRole.ASSEMBLY,
            simulation_mode=self._simulation,
            sim_place_time_min=3.0,
            sim_place_time_max=8.0,
        )
        self._master.register_robot(assembly_robot)

        # Registrar robot de corte
        cutting_robot = RobotConfig(
            name="kuka_cutting",
            role=RobotRole.CUTTING,
            simulation_mode=self._simulation,
            sim_cut_time_min=2.0,
            sim_cut_time_max=5.0,
        )
        self._master.register_robot(cutting_robot)

        if self._simulation:
            self.get_logger().info("Modo SIMULACIÓN activado — respuestas simuladas con tiempos aleatorios")
        else:
            self.get_logger().info("Modo REAL — usando CumotionClient")

        # --- cliente cumotion (MoveGroup action) ---
        self._cumotion = CumotionClient(self)
        if not self._cumotion.wait_for_server(timeout_sec=15.0):
            self.get_logger().warn(
                "MoveGroup action server no disponible al arrancar — "
                "/place_piece responderá 503 hasta que esté listo")

        # --- servidor HTTPS (recibe órdenes desde Odoo) ---
        self._job_server = JobServer(
            publisher=self.job_pub,
            logger=self.get_logger(),
            odoo=self.odoo,
            cumotion=self._cumotion,
            host=p('server_host'),
            port=p('server_port'),
            ssl_cert=p('ssl_cert'),
            ssl_key=p('ssl_key'),
        )
        self._job_server.start()

        self.get_logger().info("OdooBridgeNode iniciado ✓")

    # ------------------------------------------------------------------
    # Callbacks desde el robot
    # ------------------------------------------------------------------
    def _on_job_completed(self, msg: String):
        production_id = int(msg.data.strip())
        self.get_logger().info(f"Job completado: production_id={production_id}")
        try:
            self.odoo.complete_production(production_id)
            self.get_logger().info(f"Odoo: mrp.production {production_id} → done")
        except OdooCallError as e:
            self.get_logger().error(f"Error marcando done en Odoo: {e}")

    def _on_qty_produced(self, msg: String):
        # formato esperado: "production_id:qty"
        try:
            prod_id_str, qty_str = msg.data.strip().split(":")
            production_id = int(prod_id_str)
            qty = float(qty_str)
        except ValueError:
            self.get_logger().error(
                f"Formato inválido en /robot/qty_produced: '{msg.data}' "
                "(esperado 'production_id:qty')")
            return

        try:
            self.odoo.update_qty_produced(production_id, qty)
            self.get_logger().info(
                f"Odoo: production {production_id} qty_producing={qty}")
        except OdooCallError as e:
            self.get_logger().error(f"Error actualizando qty en Odoo: {e}")

    def _on_job_error(self, msg: String):
        # formato esperado: "production_id:mensaje de error"
        try:
            prod_id_str, error_msg = msg.data.strip().split(":", 1)
            production_id = int(prod_id_str)
        except ValueError:
            self.get_logger().error(
                f"Formato inválido en /robot/job_error: '{msg.data}'")
            return

        self.get_logger().error(
            f"Error reportado por robot en production {production_id}: {error_msg}")
        # Podés agregar aquí una llamada a Odoo para loguear el error,
        # por ejemplo escribiendo en un campo de notas o creando un chatter message.

    def _on_workorder_done(self, msg: String):
        """Robot completó una workorder individual.

        Formato esperado: "workorder_id"
        """
        try:
            workorder_id = int(msg.data.strip())
        except ValueError:
            self.get_logger().error(
                f"Formato inválido en /robot/workorder_done: '{msg.data}' "
                "(esperado 'workorder_id')")
            return

        self.get_logger().info(f"Workorder completada: wo_id={workorder_id}")
        try:
            self.odoo.complete_workorder(workorder_id)
            self.get_logger().info(
                f"Odoo: mrp.workorder {workorder_id} → done")
        except OdooCallError as e:
            self.get_logger().error(f"Error completando workorder en Odoo: {e}")

    def _on_piece_placed(self, msg: String):
        """Robot colocó una pieza en su posición final.

        Formato esperado: "component_id:production_id"
        """
        try:
            comp_id_str, prod_id_str = msg.data.strip().split(":")
            component_id = int(comp_id_str)
            production_id = int(prod_id_str)
        except ValueError:
            self.get_logger().error(
                f"Formato inválido en /robot/piece_placed: '{msg.data}' "
                "(esperado 'component_id:production_id')")
            return

        self.get_logger().info(
            f"Pieza colocada: component_id={component_id} "
            f"production_id={production_id}")
        try:
            self.odoo.update_piece_status(
                component_id=component_id,
                state="placed",
                production_id=production_id,
            )
            self.get_logger().info(
                f"Odoo: pieza {component_id} → placed")
        except OdooCallError as e:
            self.get_logger().error(f"Error actualizando pieza en Odoo: {e}")

    def _on_piece_moving(self, msg: String):
        """Robot está moviendo una pieza.

        Formato esperado: "component_id:production_id"
        """
        try:
            comp_id_str, prod_id_str = msg.data.strip().split(":")
            component_id = int(comp_id_str)
            production_id = int(prod_id_str)
        except ValueError:
            self.get_logger().error(
                f"Formato inválido en /robot/piece_moving: '{msg.data}' "
                "(esperado 'component_id:production_id')")
            return

        self.get_logger().info(
            f"Pieza en movimiento: component_id={component_id} "
            f"production_id={production_id}")
        try:
            self.odoo.update_piece_status(
                component_id=component_id,
                state="moving",
                production_id=production_id,
            )
            self.get_logger().info(
                f"Odoo: pieza {component_id} → moving")
        except OdooCallError as e:
            self.get_logger().error(f"Error actualizando pieza en Odoo: {e}")


def main(args=None):
    rclpy.init(args=args)
    executor = MultiThreadedExecutor()
    node = BridgeNode()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()