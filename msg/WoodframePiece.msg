# WoodframePiece.msg
# Datos de una pieza de woodframe enviados desde Odoo 18

std_msgs/Header header

# Identificación
string piece_id          # ID de la línea en Odoo (mrp.production o sale.order.line)
string piece_type        # "stud" | "top_plate" | "bottom_plate" | "header" | etc.
string product_name      # nombre del producto en Odoo

# Dimensiones en metros
float64 length           # largo
float64 width            # ancho
float64 height           # alto (espesor)

# Posición destino en la mesa de ensamble (frame: world o table)
geometry_msgs/Point position   # XYZ en metros

# Estado
string status            # "pending" | "in_progress" | "placed" | "error"