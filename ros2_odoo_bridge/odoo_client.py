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
