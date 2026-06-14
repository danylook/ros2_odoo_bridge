"""
job_server: servidor FastAPI con HTTPS que recibe órdenes desde Odoo 18
y las publica como topics ROS2.

Endpoints:
  POST /start_job      → publica en /robot/start_job
  POST /place_piece    → convierte datos Odoo y llama cumotion action server
  GET  /health         → healthcheck
  GET  /pending_jobs   → consulta órdenes pendientes en Odoo
"""

import json
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any

from .woodframe_utils import odoo_piece_to_ros

app = FastAPI(title="ROS2-Odoo Bridge", version="0.2.0")

# Inyectados desde BridgeNode
_publisher = None
_logger    = None
_odoo      = None
_cumotion  = None
_executor  = ThreadPoolExecutor(max_workers=2)


# ------------------------------------------------------------------
# Modelos de request
# ------------------------------------------------------------------
class JobRequest(BaseModel):
    production_id: int
    product_name:  str       = ""
    product_qty:   float     = 0.0
    cutting_list:  list[Any] = []
    extra:         dict      = {}


class Position(BaseModel):
    x: float
    y: float
    z: float


class PlacePieceRequest(BaseModel):
    """
    Payload que manda Odoo por cada pieza de woodframe.

    Ejemplo real:
      product_code : "2x6_SPF_No_2_144"
      product_name : "2x6 SPF No 2"
      cut_code     : "C_VTP_2x6_SPF_No_2_1"
      length_in    : 164.750
      width_in     : 1.500
      depth_in     : 5.500
      position     : {"x": 1.25, "y": 0.30, "z": 0.0}  ← metros, calculado por Odoo
    """
    piece_id:      str            # ID de la línea en Odoo (ej. "42-line-7")
    product_code:  str            # ej. "2x6_SPF_No_2_144"
    product_name:  str  = ""      # ej. "2x6 SPF No 2"
    cut_code:      str  = ""      # ej. "C_VTP_2x6_SPF_No_2_1"
    length_in:     float          # pulgadas decimales — largo
    width_in:      float          # pulgadas decimales — ancho (cara)
    depth_in:      float          # pulgadas decimales — profundidad/espesor
    position:      Position       # XYZ destino en metros (frame world/mesa)
    production_id: int | None = None


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "node": "ros2_odoo_bridge"}


@app.post("/start_job")
async def start_job(req: JobRequest):
    if _publisher is None:
        raise HTTPException(status_code=503, detail="Publisher ROS2 no inicializado")

    payload = {
        "production_id": req.production_id,
        "product_name":  req.product_name,
        "product_qty":   req.product_qty,
        "cutting_list":  req.cutting_list,
        "extra":         req.extra,
    }

    from std_msgs.msg import String
    msg = String()
    msg.data = json.dumps(payload)
    _publisher.publish(msg)

    if _logger:
        _logger.info(
            f"[JobServer] start_job publicado: production_id={req.production_id}, "
            f"producto='{req.product_name}', qty={req.product_qty}"
        )
        pieces = req.cutting_list
        if pieces:
            _logger.info(
                f"[JobServer]   → {len(pieces)} piezas en cutting_list:"
            )
            for p in pieces:
                _logger.info(
                    f"[JobServer]     [{p.get('data_id','?')}] "
                    f"seq={p.get('sequence','?')} "
                    f"pos=({p.get('x',0):.3f}, {p.get('y',0):.3f}) "
                    f"dim={p.get('length',0):.3f}x{p.get('width',0):.3f}x{p.get('depth',0):.3f}"
                )
        else:
            _logger.info("[JobServer]   → cutting_list vacío")
        _logger.info(
            f"[JobServer] start_job publicado: production_id={req.production_id}")

    return {"status": "accepted", "production_id": req.production_id}


@app.post("/place_piece")
async def place_piece(req: PlacePieceRequest):
    """
    Recibe datos de una pieza desde Odoo (pulgadas) y llama cumotion para posicionarla.

    Flujo interno:
      1. odoo_piece_to_ros() convierte pulgadas → metros y parsea cut_code → piece_type
      2. CumotionClient.place_piece() construye el MoveGroup goal y lo envía
      3. Se responde a Odoo de forma síncrona con el resultado
    """
    if _cumotion is None:
        raise HTTPException(status_code=503, detail="CumotionClient no inicializado")

    # Convertir payload Odoo al formato interno del bridge
    raw = {
        "piece_id":     req.piece_id,
        "product_code": req.product_code,
        "product_name": req.product_name,
        "cut_code":     req.cut_code,
        "length_in":    req.length_in,
        "width_in":     req.width_in,
        "depth_in":     req.depth_in,
        "position": {"x": req.position.x, "y": req.position.y, "z": req.position.z},
        "production_id": req.production_id,
    }
    piece_data = odoo_piece_to_ros(raw)

    if _logger:
        _logger.info(
            f"[JobServer] place_piece: '{req.piece_id}' "
            f"tipo={piece_data['piece_type']} "
            f"({req.length_in}\" x {req.width_in}\" x {req.depth_in}\") "
            f"→ ({piece_data['length']:.4f} x {piece_data['width']:.4f} x {piece_data['height']:.4f} m) "
            f"pos=({req.position.x:.3f}, {req.position.y:.3f}, {req.position.z:.3f})"
        )

    # Ejecutar en threadpool para no bloquear el event loop de FastAPI
    loop = asyncio.get_event_loop()
    success, message = await loop.run_in_executor(
        _executor, _cumotion.place_piece, piece_data
    )

    if not success:
        if _logger:
            _logger.error(f"[JobServer] place_piece failed '{req.piece_id}': {message}")
        raise HTTPException(status_code=500, detail=message)

    if _logger:
        _logger.info(f"[JobServer] place_piece OK '{req.piece_id}': {message}")

    return {
        "status":     "placed",
        "piece_id":   req.piece_id,
        "piece_type": piece_data["piece_type"],
        "dims_m": {
            "length": piece_data["length"],
            "width":  piece_data["width"],
            "height": piece_data["height"],
        },
        "message": message,
    }


# ------------------------------------------------------------------
# Clase servidor (se instancia desde BridgeNode)
# ------------------------------------------------------------------
class JobServer:
    def __init__(self, publisher, logger, odoo, cumotion,
                 host: str, port: int,
                 ssl_cert: str = "", ssl_key: str = ""):
        global _publisher, _logger, _odoo, _cumotion
        _publisher = publisher
        _logger    = logger
        _odoo      = odoo
        _cumotion  = cumotion

        self._config = uvicorn.Config(
            app=app,
            host=host,
            port=port,
            log_level="warning",
        )
        self._server = uvicorn.Server(self._config)

    def start(self):
        t = threading.Thread(target=self._server.run, daemon=True)
        t.start()
        if _logger:
            _logger.info(
                f"[JobServer] Escuchando en "
                f"http://{self._config.host}:{self._config.port}")


def main():
    """Entry point standalone (sin ROS2, para testing)."""
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
