"""
woodframe_utils.py

Conversiones y parsing de datos de piezas woodframe
tal como los manda Odoo (pulgadas decimales, códigos de operación).

Ejemplo de datos Odoo:
  product_code : "2x6_SPF_No_2_144"
  product_name : "2x6 SPF No 2"
  cut_code     : "C_VTP_2x6_SPF_No_2_1"
  length_in    : 164.750   (pulgadas)
  width_in     : 1.500
  depth_in     : 5.500
  position     : {x, y, z} (metros, calculado por Odoo)
"""

import re

INCHES_TO_METERS = 0.0254

# Mapeo de prefijos del código de corte → tipo de pieza
# Ajustar según nomenclatura de tu proyecto MiTek
CUT_CODE_TYPE_MAP = {
    "VTP": "vertical_top_plate",
    "VBP": "vertical_bottom_plate",
    "STD": "stud",
    "HDR": "header",
    "SLL": "sill",
    "CRR": "cripple",
    "BLK": "blocking",
    "RIM": "rim_joist",
    "HTP": "horizontal_top_plate",
    "HBP": "horizontal_bottom_plate",
}


def inches_to_meters(value_in: float) -> float:
    return round(value_in * INCHES_TO_METERS, 6)


def parse_piece_type(cut_code: str) -> str:
    """
    Extrae el tipo de pieza del código de operación de corte.

    Ejemplos:
      "C_VTP_2x6_SPF_No_2_1"  → "vertical_top_plate"
      "C_STD_2x4_SPF_No_2_3"  → "stud"
      "C_HDR_2x6_SPF_No_2_1"  → "header"

    Si no reconoce el prefijo retorna el token crudo (ej. "VTP").
    """
    # Formato esperado: C_<TYPE>_<resto>
    parts = cut_code.upper().split("_")
    if len(parts) >= 2 and parts[0] == "C":
        type_token = parts[1]
        return CUT_CODE_TYPE_MAP.get(type_token, type_token.lower())
    # Fallback: buscar cualquier token conocido dentro del código
    for token, piece_type in CUT_CODE_TYPE_MAP.items():
        if token in cut_code.upper():
            return piece_type
    return "unknown"


def parse_lumber_dims(product_code: str) -> dict:
    """
    Extrae dimensiones nominales del código de producto.

    "2x6_SPF_No_2_144" → {"nominal_width": 2, "nominal_depth": 6, "species": "SPF", "grade": "No_2"}
    """
    m = re.match(r"(\d+)x(\d+)_([A-Z]+)_(.+?)(?:_\d+)?$", product_code, re.IGNORECASE)
    if m:
        return {
            "nominal_width": int(m.group(1)),
            "nominal_depth": int(m.group(2)),
            "species":       m.group(3).upper(),
            "grade":         m.group(4),
        }
    return {}


def odoo_piece_to_ros(payload: dict) -> dict:
    """
    Convierte el payload de Odoo al formato interno del bridge (todo en metros).

    Payload Odoo esperado:
    {
        "piece_id":     "42-line-7",
        "product_code": "2x6_SPF_No_2_144",
        "product_name": "2x6 SPF No 2",
        "cut_code":     "C_VTP_2x6_SPF_No_2_1",
        "length_in":    164.750,
        "width_in":     1.500,
        "depth_in":     5.500,
        "position":     {"x": 1.250, "y": 0.300, "z": 0.0},
        "production_id": 42
    }

    Retorna dict listo para CumotionClient.place_piece()
    """
    piece_type = parse_piece_type(payload.get("cut_code", ""))

    return {
        "piece_id":     payload["piece_id"],
        "piece_type":   piece_type,
        "cut_code":     payload.get("cut_code", ""),
        "product_code": payload.get("product_code", ""),
        "product_name": payload.get("product_name", ""),
        # Dimensiones convertidas a metros
        "length": inches_to_meters(payload["length_in"]),   # largo
        "width":  inches_to_meters(payload["width_in"]),    # ancho (cara)
        "height": inches_to_meters(payload["depth_in"]),    # profundidad/espesor
        # Posición destino (ya viene en metros desde Odoo)
        "position": payload["position"],
        "production_id": payload.get("production_id"),
        # Info adicional para logging/debug
        "dims_original": {
            "length_in": payload["length_in"],
            "width_in":  payload["width_in"],
            "depth_in":  payload["depth_in"],
        },
    }