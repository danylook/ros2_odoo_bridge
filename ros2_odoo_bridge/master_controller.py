"""
master_controller.py — Controlador maestro del sistema ROS2-Odoo Bridge.

Gestiona la comunicación con los robots (réales o simulados), coordina
las órdenes de fabricación y reporta el progreso a Odoo en tiempo real.

Arquitectura:
    job_server.py (FastAPI)
        → MasterController
            → Robot 1 (KUKA / simulación)
            → Robot 2 (KUKA / simulación)
        → OdooClient (reporta progreso a Odoo)

Modos de operación:
    - real:    envía comandos a robots físicos vía CumotionClient
    - simular: genera respuestas simuladas con tiempos realistas
"""

import logging
import time
import threading
import json
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

_logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Enums y tipos de datos
# ──────────────────────────────────────────────────────────────────────

class RobotRole(Enum):
    """Rol de cada robot en la línea de producción."""
    CUTTING = "cutting"          # Robot de corte (sierra)
    ASSEMBLY = "assembly"        # Robot de ensamblaje/colocación


class RobotState(Enum):
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"


class PieceState(Enum):
    PENDING = "pending"
    CUTTING = "cutting"
    CUT_DONE = "cut_done"
    MOVING = "moving"
    PLACED = "placed"
    ERROR = "error"


@dataclass
class RobotConfig:
    """Configuración de un robot."""
    name: str
    role: RobotRole
    host: str = ""
    port: int = 0
    simulation_mode: bool = True
    # Tiempos simulados en segundos
    sim_cut_time_min: float = 3.0
    sim_cut_time_max: float = 8.0
    sim_place_time_min: float = 5.0
    sim_place_time_max: float = 15.0


@dataclass
class PieceJob:
    """Trabajo de una pieza individual."""
    piece_id: str
    production_id: int
    data_id: str = ""
    piece_type: str = ""
    product_code: str = ""
    product_name: str = ""
    cut_code: str = ""
    length_in: float = 0.0
    width_in: float = 0.0
    depth_in: float = 0.0
    position_x: float = 0.0
    position_y: float = 0.0
    position_z: float = 0.0
    state: PieceState = PieceState.PENDING
    assigned_robot: str = ""
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error_message: str = ""


@dataclass
class ProductionJob:
    """Orden de fabricación completa."""
    production_id: int
    product_name: str = ""
    product_qty: float = 1.0
    pieces: list = field(default_factory=list)
    state: str = "pending"  # pending, in_progress, completed, error
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


# ──────────────────────────────────────────────────────────────────────
# MasterController
# ──────────────────────────────────────────────────────────────────────

class MasterController:
    """
    Controlador maestro que orquesta los robots y reporta a Odoo.

    Modos:
        - simulation=True:  respuestas simuladas (sin robots físicos)
        - simulation=False: usa CumotionClient para robots reales
    """

    def __init__(self, odoo_client=None, simulation: bool = True):
        self._odoo = odoo_client
        self._simulation = simulation

        # Robots configurados
        self._robots: dict[str, RobotConfig] = {}
        self._robot_states: dict[str, RobotState] = {}

        # Trabajos activos
        self._productions: dict[int, ProductionJob] = {}
        self._lock = threading.Lock()

        # Callbacks externos (CumotionClient real)
        self._cumotion_clients: dict[str, object] = {}

        _logger.info(
            f"MasterController iniciado (modo={'simulación' if simulation else 'real'})"
        )

    # ──────────────────────────────────────────────────────────────────
    # Configuración de robots
    # ──────────────────────────────────────────────────────────────────

    def register_robot(self, config: RobotConfig, cumotion_client=None):
        """Registra un robot en el controlador."""
        with self._lock:
            self._robots[config.name] = config
            self._robot_states[config.name] = RobotState.IDLE
            if cumotion_client:
                self._cumotion_clients[config.name] = cumotion_client
            _logger.info(f"Robot registrado: {config.name} ({config.role.value})")

    def get_robot_state(self, name: str) -> RobotState:
        return self._robot_states.get(name, RobotState.OFFLINE)

    def set_robot_state(self, name: str, state: RobotState):
        with self._lock:
            self._robot_states[name] = state

    # ──────────────────────────────────────────────────────────────────
    # Gestión de órdenes de fabricación
    # ──────────────────────────────────────────────────────────────────

    def start_production(self, production_id: int, product_name: str = "",
                         product_qty: float = 1.0,
                         cutting_list: list = None) -> dict:
        """
        Inicia una orden de fabricación.

        Crea los trabajos de piezas y asigna a robots disponibles.
        Retorna resumen de la orden.
        """
        if cutting_list is None:
            cutting_list = []

        with self._lock:
            pieces = []
            for item in cutting_list:
                piece = PieceJob(
                    piece_id=f"{production_id}-{item.get('id', 0)}",
                    production_id=production_id,
                    data_id=item.get("data_id", ""),
                    piece_type="",
                    length_in=item.get("length", 0.0),
                    width_in=item.get("width", 0.0),
                    depth_in=item.get("depth", 0.0),
                    position_x=item.get("x", 0.0),
                    position_y=item.get("y", 0.0),
                    position_z=0.0,
                    state=PieceState.PENDING,
                )
                pieces.append(piece)

            job = ProductionJob(
                production_id=production_id,
                product_name=product_name,
                product_qty=product_qty,
                pieces=pieces,
                state="in_progress",
                started_at=time.time(),
            )
            self._productions[production_id] = job

        _logger.info(
            f"Orden {production_id} iniciada — {len(pieces)} piezas"
        )

        # Reportar a Odoo
        self._report_to_odoo(production_id, "in_progress",
                             f"Orden iniciada con {len(pieces)} piezas")

        return {
            "status": "accepted",
            "production_id": production_id,
            "piece_count": len(pieces),
        }

    def place_piece(self, piece_data: dict) -> tuple[bool, str]:
        """
        Procesa una pieza individual.

        En modo simulación: genera tiempos y respuestas simuladas.
        En modo real: delega al CumotionClient del robot asignado.

        Retorna: (success: bool, message: str)
        """
        piece_id = piece_data.get("piece_id", "")
        production_id = piece_data.get("production_id")

        # Buscar o crear el trabajo de pieza
        with self._lock:
            prod = self._productions.get(production_id)
            if not prod:
                # Auto-crear producción si no existe
                prod = ProductionJob(
                    production_id=production_id,
                    state="in_progress",
                    started_at=time.time(),
                )
                self._productions[production_id] = prod

            # Buscar pieza existente o crear una
            piece = None
            for p in prod.pieces:
                if p.piece_id == piece_id:
                    piece = p
                    break
            if not piece:
                piece = PieceJob(
                    piece_id=piece_id,
                    production_id=production_id,
                    data_id=piece_data.get("product_code", ""),
                    piece_type="",
                    length_in=piece_data.get("length_in", 0.0),
                    width_in=piece_data.get("width_in", 0.0),
                    depth_in=piece_data.get("depth_in", 0.0),
                    position_x=piece_data.get("position", {}).get("x", 0.0),
                    position_y=piece_data.get("position", {}).get("y", 0.0),
                    position_z=piece_data.get("position", {}).get("z", 0.0),
                    state=PieceState.PENDING,
                )
                prod.pieces.append(piece)

        # Asignar robot disponible
        robot_name = self._assign_robot()
        if not robot_name:
            return False, "No hay robots disponibles"

        piece.assigned_robot = robot_name
        piece.state = PieceState.MOVING
        piece.started_at = time.time()

        _logger.info(
            f"Pieza {piece_id} asignada a robot {robot_name}"
        )

        # Reportar a Odoo: pieza en movimiento
        self._report_piece_status(production_id, piece_id, "moving", robot_name)

        if self._simulation:
            # ── Modo simulación ──────────────────────────────────
            success, message = self._simulate_place_piece(piece)
        else:
            # ── Modo real ────────────────────────────────────────
            success, message = self._execute_place_piece(piece, robot_name)

        # Actualizar estado final
        with self._lock:
            piece.completed_at = time.time()
            if success:
                piece.state = PieceState.PLACED
            else:
                piece.state = PieceState.ERROR
                piece.error_message = message
            # Liberar robot
            self._robot_states[robot_name] = RobotState.IDLE

        # Reportar a Odoo: resultado final
        state_str = "placed" if success else "error"
        self._report_piece_status(production_id, piece_id, state_str, robot_name,
                                  message if not success else "")

        # Verificar si la orden completa terminó
        self._check_production_completion(production_id)

        return success, message

    def get_production_status(self, production_id: int) -> dict:
        """Retorna el estado completo de una orden."""
        with self._lock:
            prod = self._productions.get(production_id)
            if not prod:
                return {"error": "Producción no encontrada"}

            pieces_status = []
            for p in prod.pieces:
                pieces_status.append({
                    "piece_id": p.piece_id,
                    "data_id": p.data_id,
                    "state": p.state.value,
                    "robot": p.assigned_robot,
                    "started_at": p.started_at,
                    "completed_at": p.completed_at,
                    "error": p.error_message,
                })

            return {
                "production_id": prod.production_id,
                "product_name": prod.product_name,
                "state": prod.state,
                "total_pieces": len(prod.pieces),
                "placed": sum(1 for p in prod.pieces if p.state == PieceState.PLACED),
                "error": sum(1 for p in prod.pieces if p.state == PieceState.ERROR),
                "pending": sum(1 for p in prod.pieces if p.state == PieceState.PENDING),
                "pieces": pieces_status,
            }

    def get_robots_status(self) -> list:
        """Retorna estado de todos los robots."""
        result = []
        with self._lock:
            for name, config in self._robots.items():
                result.append({
                    "name": name,
                    "role": config.role.value,
                    "state": self._robot_states.get(name, RobotState.OFFLINE).value,
                    "simulation": config.simulation_mode,
                })
        return result

    # ──────────────────────────────────────────────────────────────────
    # Métodos internos
    # ──────────────────────────────────────────────────────────────────

    def _assign_robot(self) -> Optional[str]:
        """Asigna el primer robot disponible."""
        with self._lock:
            for name, state in self._robot_states.items():
                if state == RobotState.IDLE:
                    self._robot_states[name] = RobotState.BUSY
                    return name
        return None

    def _simulate_place_piece(self, piece: PieceJob) -> tuple[bool, str]:
        """
        Simula la colocación de una pieza con tiempos realistas.

        Genera un tiempo aleatorio entre sim_place_time_min y sim_place_time_max.
        """
        import random

        robot_config = self._robots.get(piece.assigned_robot)
        if robot_config:
            delay = random.uniform(
                robot_config.sim_place_time_min,
                robot_config.sim_place_time_max,
            )
        else:
            delay = random.uniform(5.0, 10.0)

        _logger.info(
            f"  [SIM] Robot {piece.assigned_robot} colocando pieza "
            f"{piece.piece_id}... ({delay:.1f}s)"
        )

        # Reportar progreso durante la simulación
        self._report_piece_status(
            piece.production_id, piece.piece_id, "moving",
            piece.assigned_robot, f"Posicionando... estimado {delay:.0f}s"
        )

        time.sleep(delay)

        # 5% de probabilidad de error simulado
        if random.random() < 0.05:
            return False, "Error simulado: posición fuera de tolerancia"

        return True, f"Pieza colocada en {delay:.1f}s"

    def _execute_place_piece(self, piece: PieceJob, robot_name: str) -> tuple[bool, str]:
        """Ejecuta la colocación real usando CumotionClient."""
        cumotion = self._cumotion_clients.get(robot_name)
        if not cumotion:
            return False, f"Robot {robot_name} no tiene CumotionClient configurado"

        piece_data = {
            "piece_id": piece.piece_id,
            "piece_type": piece.piece_type,
            "product_name": piece.product_name,
            "length": piece.length_in * 0.0254,  # in → m
            "width": piece.width_in * 0.0254,
            "height": piece.depth_in * 0.0254,
            "position": {
                "x": piece.position_x,
                "y": piece.position_y,
                "z": piece.position_z,
            },
            "production_id": piece.production_id,
        }

        return cumotion.place_piece(piece_data)

    def _report_to_odoo(self, production_id: int, state: str, message: str = ""):
        """Reporta estado de la orden a Odoo vía chatter."""
        if not self._odoo:
            return
        try:
            self._odoo.call(
                "mrp.production", "message_post",
                [[production_id]],
                {
                    "body": f"<p><b>ROS2 Bridge:</b> {message}</p>",
                    "subtype_xmlid": "mail.mt_note",
                },
            )
        except Exception as e:
            _logger.warning(f"Error reportando a Odoo: {e}")

    def _report_piece_status(self, production_id: int, piece_id: str,
                              state: str, robot: str = "", message: str = ""):
        """Reporta estado de una pieza a Odoo."""
        if not self._odoo:
            return
        try:
            # Actualizar wf.ros2.piece.status si existe
            self._odoo.call(
                "wf.ros2.piece.status", "search",
                [[["production_id", "=", production_id],
                  ["component_id.data_id", "=", piece_id]]],
            )
        except Exception:
            pass  # El modelo puede no existir aún

    def _check_production_completion(self, production_id: int):
        """Verifica si todas las piezas de una orden están completadas."""
        with self._lock:
            prod = self._productions.get(production_id)
            if not prod:
                return

            all_done = all(
                p.state in (PieceState.PLACED, PieceState.ERROR)
                for p in prod.pieces
            )
            if all_done:
                prod.state = "completed"
                prod.completed_at = time.time()
                total = len(prod.pieces)
                placed = sum(1 for p in prod.pieces if p.state == PieceState.PLACED)
                errors = sum(1 for p in prod.pieces if p.state == PieceState.ERROR)

                msg = (
                    f"Orden completada: {placed}/{total} colocadas"
                    + (f", {errors} con error" if errors else "")
                )
                _logger.info(f"Orden {production_id} completada: {msg}")
                self._report_to_odoo(production_id, "completed", msg)

                # Marcar como done en Odoo si todo ok
                if errors == 0 and self._odoo:
                    try:
                        self._odoo.mark_production_done(production_id)
                    except Exception as e:
                        _logger.warning(f"Error marcando done en Odoo: {e}")
