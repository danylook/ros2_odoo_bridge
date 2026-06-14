"""
test_woodframe_utils.py

Tests para conversión y parsing de datos woodframe.
Ejecutar con: pytest test/test_woodframe_utils.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ros2_odoo_bridge.woodframe_utils import (
    inches_to_meters,
    parse_piece_type,
    parse_lumber_dims,
    odoo_piece_to_ros,
)

# ------------------------------------------------------------------
# Datos del ejemplo real de Odoo
# ------------------------------------------------------------------
EXAMPLE_PAYLOAD = {
    "piece_id":     "42-line-1",
    "product_code": "2x6_SPF_No_2_144",
    "product_name": "2x6 SPF No 2",
    "cut_code":     "C_VTP_2x6_SPF_No_2_1",
    "length_in":    164.750,
    "width_in":     1.500,
    "depth_in":     5.500,
    "position":     {"x": 1.250, "y": 0.300, "z": 0.0},
    "production_id": 42,
}


def test_inches_to_meters():
    assert abs(inches_to_meters(1.0)    - 0.0254)  < 1e-6
    assert abs(inches_to_meters(164.75) - 4.18465) < 1e-4
    assert abs(inches_to_meters(1.5)    - 0.0381)  < 1e-6
    assert abs(inches_to_meters(5.5)    - 0.1397)  < 1e-6


def test_parse_piece_type_vtp():
    assert parse_piece_type("C_VTP_2x6_SPF_No_2_1") == "vertical_top_plate"


def test_parse_piece_type_std():
    assert parse_piece_type("C_STD_2x4_SPF_No_2_3") == "stud"


def test_parse_piece_type_hdr():
    assert parse_piece_type("C_HDR_2x6_SPF_No_2_1") == "header"


def test_parse_piece_type_unknown():
    result = parse_piece_type("C_XYZ_2x4_1")
    assert result == "xyz"   # retorna token crudo en minúscula


def test_parse_lumber_dims():
    dims = parse_lumber_dims("2x6_SPF_No_2_144")
    assert dims["nominal_width"] == 2
    assert dims["nominal_depth"] == 6
    assert dims["species"] == "SPF"


def test_odoo_piece_to_ros_conversions():
    result = odoo_piece_to_ros(EXAMPLE_PAYLOAD)

    # Tipo de pieza parseado correctamente
    assert result["piece_type"] == "vertical_top_plate"

    # Conversión de dimensiones
    assert abs(result["length"] - 4.18465) < 1e-4   # 164.750" → 4.18465 m
    assert abs(result["width"]  - 0.0381)  < 1e-4   # 1.500"   → 0.0381 m
    assert abs(result["height"] - 0.1397)  < 1e-4   # 5.500"   → 0.1397 m

    # Posición sin modificar (Odoo manda metros)
    assert result["position"]["x"] == 1.250
    assert result["position"]["y"] == 0.300
    assert result["position"]["z"] == 0.0

    # Datos originales preservados
    assert result["dims_original"]["length_in"] == 164.750
    assert result["cut_code"] == "C_VTP_2x6_SPF_No_2_1"
    assert result["production_id"] == 42


def test_odoo_piece_to_ros_ids():
    result = odoo_piece_to_ros(EXAMPLE_PAYLOAD)
    assert result["piece_id"]     == "42-line-1"
    assert result["product_code"] == "2x6_SPF_No_2_144"
    assert result["product_name"] == "2x6 SPF No 2"


if __name__ == "__main__":
    test_inches_to_meters()
    test_parse_piece_type_vtp()
    test_parse_piece_type_std()
    test_parse_piece_type_hdr()
    test_parse_piece_type_unknown()