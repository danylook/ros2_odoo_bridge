"""
cumotion_client.py

Action client que convierte datos de una WoodframePiece en un goal
para isaac_ros_cumotion a través del action server MoveGroup de MoveIt2.

Flujo:
  WoodframePiece
    → construir PoseStamped con la posición destino
      → enviar MoveGroup goal con planning_pipeline="cumotion"
        → esperar resultado
          → retornar éxito/error
"""

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node

from geometry_msgs.msg import PoseStamped, Point, Quaternion
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import (
    MotionPlanRequest,
    Constraints,
    PositionConstraint,
    OrientationConstraint,
    BoundingVolume,
    WorkspaceParameters,
)
from shape_msgs.msg import SolidPrimitive
from std_msgs.msg import Header

# Tolerancias de posición (metros) y orientación (rad)
POSITION_TOLERANCE  = 0.005   # 5 mm
ORIENTATION_TOLERANCE = 0.05  # ~3°

# Nombre del grupo de planificación en tu SRDF (ajustar si cambia)
PLANNING_GROUP = "manipulator"

# Frame de referencia de la mesa de ensamble
REFERENCE_FRAME = "world"


class CumotionClient:
    """
    Wrapper sobre el ActionClient de MoveGroup para isaac_ros_cumotion.
    Se instancia pasando el nodo ROS2 activo.
    """

    def __init__(self, node: Node):
        self._node = node
        self._client = ActionClient(node, MoveGroup, "/move_action")
        self._logger = node.get_logger()

    def wait_for_server(self, timeout_sec: float = 10.0) -> bool:
        self._logger.info("Esperando MoveGroup action server...")
        ready = self._client.wait_for_server(timeout_sec=timeout_sec)
        if ready:
            self._logger.info("MoveGroup action server listo ✓")
        else:
            self._logger.error("MoveGroup action server no disponible")
        return ready

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def place_piece(self, piece_data: dict) -> tuple[bool, str]:
        """
        Planifica y ejecuta el movimiento para posicionar una pieza.

        piece_data keys:
          piece_id, piece_type, product_name
          length, width, height  (metros)
          position: {x, y, z}   (metros, frame=world)

        Retorna: (success: bool, message: str)
        """
        goal = self._build_goal(piece_data)

        self._logger.info(
            f"Enviando goal cumotion para pieza '{piece_data.get('piece_id')}' "
            f"→ pos=({piece_data['position']['x']:.3f}, "
            f"{piece_data['position']['y']:.3f}, "
            f"{piece_data['position']['z']:.3f})"
        )

        future = self._client.send_goal_async(goal)
        rclpy.spin_until_future_complete(self._node, future, timeout_sec=30.0)

        goal_handle = future.result()
        if goal_handle is None or not goal_handle.accepted:
            return False, "Goal rechazado por MoveGroup"

        result_future = goal_handle.get_result_async()
        rclpy.spin_until_future_complete(self._node, result_future, timeout_sec=60.0)

        result = result_future.result()
        if result is None:
            return False, "Timeout esperando resultado de MoveGroup"

        error_code = result.result.error_code.val
        # MoveItErrorCodes.SUCCESS = 1
        if error_code == 1:
            return True, "Pieza posicionada correctamente"
        else:
            return False, f"MoveGroup error code: {error_code}"

    # ------------------------------------------------------------------
    # Construcción del goal MoveGroup
    # ------------------------------------------------------------------
    def _build_goal(self, piece_data: dict) -> MoveGroup.Goal:
        pos = piece_data["position"]

        target_pose = PoseStamped()
        target_pose.header = Header()
        target_pose.header.frame_id = REFERENCE_FRAME
        target_pose.header.stamp = self._node.get_clock().now().to_msg()
        target_pose.pose.position = Point(
            x=float(pos["x"]),
            y=float(pos["y"]),
            z=float(pos["z"]) + float(piece_data.get("height", 0.0)) / 2.0
            # offset z para que el TCP llegue encima de la pieza
        )
        # Orientación neutra (tool apuntando hacia abajo)
        target_pose.pose.orientation = Quaternion(x=0.0, y=1.0, z=0.0, w=0.0)

        # --- Constraint de posición ---
        pos_constraint = PositionConstraint()
        pos_constraint.header = target_pose.header
        pos_constraint.link_name = "tool0"   # ajustar al link TCP de tu KUKA
        pos_constraint.target_point_offset.x = 0.0
        pos_constraint.target_point_offset.y = 0.0
        pos_constraint.target_point_offset.z = 0.0

        bv = BoundingVolume()
        sp = SolidPrimitive()
        sp.type = SolidPrimitive.SPHERE
        sp.dimensions = [POSITION_TOLERANCE]
        bv.primitives = [sp]
        bv.primitive_poses = [target_pose.pose]
        pos_constraint.constraint_region = bv
        pos_constraint.weight = 1.0

        # --- Constraint de orientación ---
        ori_constraint = OrientationConstraint()
        ori_constraint.header = target_pose.header
        ori_constraint.link_name = "tool0"
        ori_constraint.orientation = target_pose.pose.orientation
        ori_constraint.absolute_x_axis_tolerance = ORIENTATION_TOLERANCE
        ori_constraint.absolute_y_axis_tolerance = ORIENTATION_TOLERANCE
        ori_constraint.absolute_z_axis_tolerance = ORIENTATION_TOLERANCE
        ori_constraint.weight = 1.0

        constraints = Constraints()
        constraints.position_constraints  = [pos_constraint]
        constraints.orientation_constraints = [ori_constraint]

        # --- Motion plan request ---
        request = MotionPlanRequest()
        request.group_name = PLANNING_GROUP
        request.goal_constraints = [constraints]
        request.num_planning_attempts = 5
        request.allowed_planning_time = 10.0
        request.max_velocity_scaling_factor = 0.5
        request.max_acceleration_scaling_factor = 0.3
        # Forzar cumotion como pipeline de planificación
        request.pipeline_id = "cumotion"
        request.planner_id  = "cuMotion"

        workspace = WorkspaceParameters()
        workspace.header = target_pose.header
        workspace.min_corner.x = -2.5
        workspace.min_corner.y = -2.5
        workspace.min_corner.z =  0.0
        workspace.max_corner.x =  2.5
        workspace.max_corner.y =  2.5
        workspace.max_corner.z =  2.5
        request.workspace_parameters = workspace

        goal = MoveGroup.Goal()
        goal.request = request
        goal.planning_options.plan_only = False          # planificar Y ejecutar
        goal.planning_options.replan = True
        goal.planning_options.replan_attempts = 3

        return goal