"""
OdooClient: cliente JSON-RPC para Odoo 18 sobre HTTPS.
Mantiene sesión autenticada y reintenta en caso de expiración.
"""
import logging
import requests

logger = logging.getLogger(__name__)


class OdooAuthError(Exception):
    pass


class OdooCallError(Exception):
    pass


class OdooClient:
    def __init__(self, url: str, db: str, user: str, password: str):
        self.url = url.rstrip('/')
        self.db = db
        self.user = user
        self.password = password
        self.session = requests.Session()
        self.uid: int | None = None
        self._authenticate()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def _authenticate(self):
        try:
            resp = self.session.post(
                f"{self.url}/web/session/authenticate",
                json={
                    "jsonrpc": "2.0",
                    "method": "call",
                    "id": 1,
                    "params": {
                        "db": self.db,
                        "login": self.user,
                        "password": self.password,
                    },
                },
                timeout=10,
            )
            resp.raise_for_status()
            result = resp.json().get("result", {})
            uid = result.get("uid")
            if not uid:
                raise OdooAuthError(f"Login fallido para {self.user}@{self.db}")
            self.uid = uid
            logger.info(f"[OdooClient] Autenticado uid={uid}")
        except requests.RequestException as e:
            raise OdooAuthError(f"Error de conexión con Odoo: {e}") from e

    # ------------------------------------------------------------------
    # Llamada genérica
    # ------------------------------------------------------------------
    def call(self, model: str, method: str, args: list, kwargs: dict | None = None):
        if kwargs is None:
            kwargs = {}
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "id": 1,
            "params": {
                "model": model,
                "method": method,
                "args": args,
                "kwargs": kwargs,
            },
        }
        try:
            resp = self.session.post(
                f"{self.url}/web/dataset/call_kw",
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise OdooCallError(f"HTTP error: {e}") from e

        body = resp.json()
        if "error" in body:
            # Sesión expirada → reautenticar y reintentar una vez
            err = body["error"]
            if err.get("code") == 100:  # session expired
                logger.warning("[OdooClient] Sesión expirada, reautenticando...")
                self._authenticate()
                return self.call(model, method, args, kwargs)
            raise OdooCallError(f"Odoo error: {err.get('data', {}).get('message', err)}")

        return body.get("result")

    # ------------------------------------------------------------------
    # Helpers mrp.production
    # ------------------------------------------------------------------
    def get_production(self, production_id: int) -> dict:
        results = self.call(
            "mrp.production", "read",
            [[production_id]],
            {"fields": ["id", "name", "state", "product_id",
                        "product_qty", "qty_producing"]},
        )
        if not results:
            raise OdooCallError(f"mrp.production {production_id} no encontrada")
        return results[0]

    def update_qty_produced(self, production_id: int, qty: float):
        return self.call(
            "mrp.production", "write",
            [[production_id], {"qty_producing": qty}],
        )

    def mark_production_done(self, production_id: int):
        return self.call(
            "mrp.production", "button_mark_done",
            [[production_id]],
        )

    def set_production_state(self, production_id: int, state: str):
        """state: 'progress' | 'to_close' | 'done'"""
        return self.call(
            "mrp.production", "write",
            [[production_id], {"state": state}],
        )

    def post_inventory(self, production_id: int):
        """Valida los movimientos de stock de la MO."""
        return self.call(
            "mrp.production", "post_inventory",
            [[production_id]],
        )

    def complete_production(self, production_id: int):
        """Valida stock y marca la MO como done."""
        self.post_inventory(production_id)
        return self.mark_production_done(production_id)

    # ------------------------------------------------------------------
    # Helpers mrp.workorder — completar por pieza
    # ------------------------------------------------------------------
    def get_workorders(self, production_id: int) -> list[dict]:
        """Retorna las workorders de una MO."""
        wo_ids = self.call(
            "mrp.workorder", "search",
            [[["production_id", "=", production_id]]],
        )
        if not wo_ids:
            return []
        return self.call(
            "mrp.workorder", "read",
            [wo_ids],
            {"fields": ["id", "name", "state", "workcenter_id",
                        "operation_id", "wf_instruction"]},
        )

    def start_workorder(self, workorder_id: int):
        """Inicia una workorder (button_start)."""
        return self.call(
            "mrp.workorder", "button_start",
            [[workorder_id]],
        )

    def complete_workorder(self, workorder_id: int):
        """Completa una workorder (button_done)."""
        return self.call(
            "mrp.workorder", "button_done",
            [[workorder_id]],
        )

    def get_pending_workorders(self, production_id: int) -> list[dict]:
        """Retorna workorders no completadas de una MO."""
        wo_ids = self.call(
            "mrp.workorder", "search",
            [[["production_id", "=", production_id],
              ["state", "!=", "done"]]],
        )
        if not wo_ids:
            return []
        return self.call(
            "mrp.workorder", "read",
            [wo_ids],
            {"fields": ["id", "name", "state", "workcenter_id"]},
        )

    # ------------------------------------------------------------------
    # Helpers wf.ros2.piece.status — tracking de piezas
    # ------------------------------------------------------------------
    def update_piece_status(self, component_id: int, state: str,
                            production_id: int | None = None,
                            robot_id: str = "",
                            x: float = 0.0, y: float = 0.0, z: float = 0.0,
                            note: str = ""):
        """Actualiza el estado de colocación de una pieza.

        state: 'pending' | 'moving' | 'placed' | 'error'
        """
        domain = [["component_id", "=", component_id]]
        if production_id:
            domain.append(["production_id", "=", production_id])

        existing = self.call("wf.ros2.piece.status", "search", [domain], {"limit": 1})

        vals = {
            "state": state,
            "robot_id": robot_id,
            "note": note,
            "x_actual": x,
            "y_actual": y,
            "z_actual": z,
        }
        if existing:
            return self.call("wf.ros2.piece.status", "write", [existing, vals])
        else:
            vals["component_id"] = component_id
            if production_id:
                vals["production_id"] = production_id
            return self.call("wf.ros2.piece.status", "create", [vals])

    def ensure_piece_statuses(self, production_id: int) -> int:
        """Crea registros de estado 'pending' para todas las piezas de la MO.

        Retorna la cantidad de registros creados.
        """
        # Obtener la sección del panel vinculada a la MO
        prod = self.call(
            "mrp.production", "read",
            [[production_id]],
            {"fields": ["panel_section_id"]},
        )
        if not prod or not prod[0].get("panel_section_id"):
            return 0

        section_id = prod[0]["panel_section_id"][0]

        # Obtener componentes de la sección
        comp_ids = self.call(
            "wf.panel.component", "search",
            [[["section_id", "=", section_id]]],
        )
        if not comp_ids:
            return 0

        # Verificar qué piezas ya tienen registro de estado
        existing = self.call(
            "wf.ros2.piece.status", "search",
            [[["section_id", "=", section_id],
              ["production_id", "=", production_id]]],
        )
        existing_comp_ids = set()
        if existing:
            records = self.call(
                "wf.ros2.piece.status", "read",
                [existing],
                {"fields": ["component_id"]},
            )
            existing_comp_ids = {r["component_id"][0] for r in records}

        # Crear registros faltantes
        to_create = []
        for comp_id in comp_ids:
            if comp_id not in existing_comp_ids:
                to_create.append({
                    "component_id": comp_id,
                    "production_id": production_id,
                    "state": "pending",
                })

        if to_create:
            self.call("wf.ros2.piece.status", "create", [to_create])

        return len(to_create)

    # ------------------------------------------------------------------
    # Helpers stock.move (para registrar movimientos de material)
    # ------------------------------------------------------------------
    def get_pending_productions(self) -> list[dict]:
        """Retorna órdenes confirmadas pendientes de ejecutar."""
        ids = self.call(
            "mrp.production", "search",
            [[["state", "in", ["confirmed", "progress"]]]],
        )
        if not ids:
            return []
        return self.call(
            "mrp.production", "read",
            [ids],
            {"fields": ["id", "name", "state", "product_id",
                        "product_qty", "date_start"]},
        )