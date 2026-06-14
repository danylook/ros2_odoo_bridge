# ROS2 ↔ Odoo 18 Bridge

**Versión:** 0.1.0  
**Repositorio:** [github.com/danylook/ros2_odoo_bridge](https://github.com/danylook/ros2_odoo_bridge)

Paquete ROS2 Jazzy que conecta **Odoo 18** con sistemas robóticos vía **FastAPI/HTTPS** y **MoveIt2/cuMotion**. Actúa como puente entre las órdenes de fabricación de Odoo y el robot KUKA para la colocación de piezas woodframe.

---

## 📁 Estructura del paquete

```
ros2_odoo_bridge/
├── package.xml                          → Declaración del paquete ROS2
├── setup.py                             → Build configuration
├── setup.cfg
├── requirements.txt                     → Dependencias Python
├── config/
│   └── params.yaml                      → Parámetros de configuración
├── launch/
│   └── bridge.launch.py                 → Launch file para ros2 launch
├── msg/
│   └── WoodframePiece.msg               → Mensaje ROS2 personalizado
├── resource/
├── ros2_odoo_bridge/
│   ├── __init__.py
│   ├── bridge_node.py                   → Nodo ROS2 principal
│   ├── odoo_client.py                   → Cliente JSON-RPC para Odoo
│   ├── job_server.py                    → Servidor FastAPI (endpoints HTTP)
│   ├── cumotion_client.py              → ActionClient MoveGroup (cuMotion)
│   └── woodframe_utils.py              → Conversiones pulgadas↔metros
└── test/
    └── test_woodframe_utils.py          → Tests unitarios
```

---

## 🧩 Módulos Python

### `bridge_node.py` — `BridgeNode`

**Clase:** `BridgeNode(rclpy.node.Node)`  
**Entry point:** `main()` — inicia el nodo ROS2 con `MultiThreadedExecutor`

Nodo ROS2 principal que orquesta todos los componentes del bridge.

| Método | Descripción |
|---|---|
| `__init__()` | Declara parámetros ROS2, autentica con Odoo, crea publisher `/robot/start_job`, suscribe topics del robot, inicializa `CumotionClient` y arranca `JobServer` |
| `_on_job_completed(msg)` | **Callback** — robot completa trabajo. Parsea `production_id` del topic `/robot/job_completed` y llama `odoo.mark_production_done()` |
| `_on_qty_produced(msg)` | **Callback** — robot reporta cantidad. Formato `"production_id:qty"` en `/robot/qty_produced`. Llama `odoo.update_qty_produced()` |
| `_on_job_error(msg)` | **Callback** — robot reporta error. Formato `"production_id:mensaje"` en `/robot/job_error`. Loguea el error |

**Parámetros ROS2 declarados:**

| Parámetro | Default | Descripción |
|---|---|---|
| `odoo_url` | `https://odoo.tudominio.com` | URL de Odoo |
| `odoo_db` | `produccion` | Base de datos Odoo |
| `odoo_user` | `robot@tudominio.com` | Usuario Odoo |
| `odoo_password` | `""` | Contraseña |
| `ssl_cert` | `/etc/ssl/ros2pc/cert.pem` | Certificado SSL |
| `ssl_key` | `/etc/ssl/ros2pc/key.pem` | Clave SSL |
| `server_host` | `0.0.0.0` | Host del servidor HTTP |
| `server_port` | `8000` | Puerto del servidor HTTP |

**Topics:**

| Topic | Dirección | Tipo | Descripción |
|---|---|---|---|
| `/robot/start_job` | Bridge → Robot | `std_msgs/String` | JSON con orden de fabricación |
| `/robot/job_completed` | Robot → Bridge | `std_msgs/String` | `"production_id"` |
| `/robot/qty_produced` | Robot → Bridge | `std_msgs/String` | `"production_id:qty"` |
| `/robot/job_error` | Robot → Bridge | `std_msgs/String` | `"production_id:mensaje"` |

---

### `odoo_client.py` — `OdooClient`

**Clase:** `OdooClient`  
**Excepciones:** `OdooAuthError`, `OdooCallError`

Cliente JSON-RPC para comunicarse con Odoo 18 vía HTTPS. Mantiene sesión autenticada con reintento automático en caso de expiración.

| Método | Descripción |
|---|---|
| `__init__(url, db, user, password)` | Configura sesión HTTP y autentica |
| `_authenticate()` | POST a `/web/session/authenticate` con credenciales. Lanza `OdooAuthError` si falla |
| `call(model, method, args, kwargs)` | **Llamada genérica** a `/web/dataset/call_kw`. Reautentica automáticamente si la sesión expira (código 100) |
| `get_production(production_id)` | Lee una MO: `id, name, state, product_id, product_qty, qty_producing` |
| `update_qty_produced(production_id, qty)` | Actualiza `qty_producing` de una MO |
| `mark_production_done(production_id)` | Llama `button_mark_done()` en la MO |
| `set_production_state(production_id, state)` | Cambia estado de la MO (`progress`, `to_close`, `done`) |
| `get_pending_productions()` | Busca MOs en estado `confirmed` o `progress` |

---

### `job_server.py` — `JobServer`

**Clase:** `JobServer`  
**Framework:** FastAPI + uvicorn  
**Entry point:** `main()` — standalone para testing sin ROS2

Servidor HTTP que recibe órdenes desde Odoo y las publica como topics ROS2 o las envía al robot vía cuMotion.

#### Modelos Pydantic (request validation)

| Modelo | Campos | Descripción |
|---|---|---|
| `JobRequest` | `production_id`, `product_name`, `product_qty`, `cutting_list`, `extra` | Payload de orden de fabricación |
| `Position` | `x`, `y`, `z` | Posición 3D en metros |
| `PlacePieceRequest` | `piece_id`, `product_code`, `product_name`, `cut_code`, `length_in`, `width_in`, `depth_in`, `position`, `production_id` | Payload de pieza individual |

#### Endpoints HTTP

| Método | Ruta | Descripción |
|---|---|---|
| **GET** | `/health` | Healthcheck → `{"status":"ok","node":"ros2_odoo_bridge"}` |
| **POST** | `/start_job` | Recibe orden de Odoo y publica en `/robot/start_job` |
| **POST** | `/place_piece` | Recibe pieza individual, convierte pulgadas→metros, llama cuMotion para posicionar (síncrono) |
| **GET** | `/pending_jobs` | Consulta MOs pendientes en Odoo |

#### Métodos de `JobServer`

| Método | Descripción |
|---|---|
| `__init__(publisher, logger, odoo, cumotion, host, port, ssl_cert, ssl_key)` | Configura uvicorn con los parámetros recibidos |
| `start()` | Lanza el servidor uvicorn en un thread separado (daemon) |

#### Endpoints detallados

**`POST /start_job`** — Enviado desde Odoo:

```json
{
  "production_id": 42,
  "product_name": "Panel WF-001",
  "product_qty": 1.0,
  "cutting_list": [
    {"id": 1, "data_id": "C_VTP_...", "sequence": 60, "x": 100.0, "y": 221.75, "length": 164.75, "width": 1.5, "depth": 5.5}
  ]
}
```

**`POST /place_piece`** — Enviado desde Odoo por cada pieza (síncrono):

```json
{
  "piece_id": "42-line-7",
  "product_code": "2x6_SPF_No_2_144",
  "product_name": "2x6 SPF No 2",
  "cut_code": "C_VTP_2x6_SPF_No_2_1",
  "length_in": 164.750,
  "width_in": 1.500,
  "depth_in": 5.500,
  "position": {"x": 1.250, "y": 0.300, "z": 0.0},
  "production_id": 42
}
```

Respuesta exitosa:
```json
{
  "status": "placed",
  "piece_id": "42-line-7",
  "piece_type": "vertical_top_plate",
  "dims_m": {"length": 4.185, "width": 0.038, "height": 0.140},
  "message": "Pieza posicionada correctamente"
}
```

---

### `cumotion_client.py` — `CumotionClient`

**Clase:** `CumotionClient`

ActionClient de MoveGroup para **isaac_ros_cumotion** (MoveIt2). Convierte datos de una pieza en un goal de MoveGroup y lo envía al action server `/move_action`.

| Método | Descripción |
|---|---|
| `__init__(node)` | Crea `ActionClient` para `/move_action` |
| `wait_for_server(timeout_sec)` | Espera a que el action server esté disponible. Retorna `bool` |
| `place_piece(piece_data)` | **API pública** — construye goal, lo envía y espera resultado. Retorna `(success, message)` |
| `_build_goal(piece_data)` | Construye el `MoveGroup.Goal` con constraints de posición y orientación |

#### Parámetros configurables (constantes de módulo)

| Constante | Valor | Descripción |
|---|---|---|
| `POSITION_TOLERANCE` | 0.005 m (5 mm) | Tolerancia de posición |
| `ORIENTATION_TOLERANCE` | 0.05 rad (~3°) | Tolerancia de orientación |
| `PLANNING_GROUP` | `"manipulator"` | Grupo de planificación SRDF |
| `REFERENCE_FRAME` | `"world"` | Frame de referencia |

#### Goal enviado a MoveGroup

El goal incluye:
- **Pose target**: posición destino + offset Z (altura de pieza / 2) para que el TCP quede sobre la pieza
- **Orientación**: tool pointing down (`x=0, y=1, z=0, w=0`)
- **Position constraint**: esfera de tolerancia 5 mm
- **Orientation constraint**: tolerancia 0.05 rad
- **Workspace**: caja de [-2.5, -2.5, 0] a [2.5, 2.5, 2.5] metros
- **Pipeline**: `cumotion` / `cuMotion`
- **Plan + execute**: `plan_only=false`, `replan=true`, 3 reintentos

---

### `woodframe_utils.py` — Utilidades de conversión

Módulo de funciones para convertir datos de piezas woodframe desde el formato de Odoo (pulgadas decimales, códigos MiTek) al formato interno del bridge (metros).

| Función | Descripción |
|---|---|
| `inches_to_meters(value_in)` | Convierte pulgadas a metros (× 0.0254) |
| `parse_piece_type(cut_code)` | Extrae tipo de pieza del código de corte MiTek (`C_VTP_...` → `vertical_top_plate`) |
| `parse_lumber_dims(product_code)` | Extrae dimensiones nominales del código de producto (`2x6_SPF_No_2_144` → `{nominal_width: 2, nominal_depth: 6, ...}`) |
| `odoo_piece_to_ros(payload)` | **Función principal** — convierte payload completo de Odoo a dict listo para `CumotionClient.place_piece()` |

#### Mapeo de códigos de corte → tipo de pieza

| Prefijo MiTek | Tipo de pieza |
|---|---|
| `VTP` | `vertical_top_plate` |
| `VBP` | `vertical_bottom_plate` |
| `STD` | `stud` |
| `HDR` | `header` |
| `SLL` | `sill` |
| `CRR` | `cripple` |
| `BLK` | `blocking` |
| `RIM` | `rim_joist` |
| `HTP` | `horizontal_top_plate` |
| `HBP` | `horizontal_bottom_plate` |

#### Conversión de unidades

| Campo Odoo | Unidad | Convertido a |
|---|---|---|
| `length_in` | pulgadas → `length` (metros) | × 0.0254 |
| `width_in` | pulgadas → `width` (metros) | × 0.0254 |
| `depth_in` | pulgadas → `height` (metros) | × 0.0254 |
| `position` | metros (ya viene en metros) | sin conversión |

---

## 📨 Mensaje ROS2 personalizado: `WoodframePiece.msg`

```msg
# WoodframePiece.msg
std_msgs/Header header

string piece_id          # ID de la línea en Odoo
string piece_type        # "stud" | "top_plate" | "bottom_plate" | etc.
string product_name      # nombre del producto en Odoo

float64 length           # largo en metros
float64 width            # ancho en metros
float64 height           # alto/espesor en metros

geometry_msgs/Point position   # XYZ destino en metros

string status            # "pending" | "in_progress" | "placed" | "error"
```

---

## ⚙️ Configuración

### `config/params.yaml`

```yaml
odoo_bridge:
  ros__parameters:
    odoo_url:      "https://wally.ecolight.com.uy"
    odoo_db:       "wally"
    odoo_user:     "info@ecolight.com.uy"
    odoo_password: "admin_ecolight"
    ssl_cert:      "/home/eco/Proyecto_102/ssl/cert.pem"
    ssl_key:       "/home/eco/Proyecto_102/ssl/key.pem"
    server_host:   "0.0.0.0"
    server_port:   8000
```

### `launch/bridge.launch.py`

Lanza el nodo `bridge_node` con los parámetros de `params.yaml`:

```bash
ros2 launch ros2_odoo_bridge bridge.launch.py
```

---

## 🔄 Arquitectura y flujo de datos

```
Odoo 18 (wally.ecolight.com.uy)
    │
    │ POST /start_job (MO completa + cutting_list)
    │ POST /place_piece (pieza individual, síncrono)
    │ GET  /pending_jobs
    ▼
┌──────────────────────────────────────────┐
│         ROS2 Bridge (FastAPI)             │
│  https://ros2.ecolight.com.uy:8000       │
│                                          │
│  job_server.py (uvicorn)                 │
│    → /start_job → publica /robot/start_job│
│    → /place_piece → llama CumotionClient  │
│    → /health, /pending_jobs              │
│                                          │
│  bridge_node.py (rclpy)                  │
│    pub: /robot/start_job                 │
│    sub: /robot/job_completed             │
│    sub: /robot/qty_produced              │
│    sub: /robot/job_error                 │
│                                          │
│  CumotionClient                          │
│    → /move_action (MoveIt2 + cuMotion)   │
└──────────────────┬───────────────────────┘
                   │
                   ▼
         KUKA Robot + MoveIt2
         + Isaac ROS / cuMotion
```

### Flujo completo

```
1. Odoo → POST /start_job
   → Bridge publica en /robot/start_job
   → Robot recibe cutting_list

2. Odoo → POST /place_piece (por cada pieza)
   → woodframe_utils.odoo_piece_to_ros() convierte pulgadas→metros
   → CumotionClient.place_piece() envía goal a MoveGroup
   → Robot posiciona la pieza
   → Responde a Odoo: {status: "placed"}

3. Robot → /robot/job_completed
   → Bridge llama odoo.mark_production_done()

4. Robot → /robot/qty_produced
   → Bridge llama odoo.update_qty_produced()

5. Robot → /robot/job_error
   → Bridge loguea el error
```

---

## 🧪 Tests

```bash
cd ~/ros2_ws
source install/setup.bash
pytest src/ros2_odoo_bridge/test/test_woodframe_utils.py -v
```

Tests incluidos:
- `test_inches_to_meters()` — conversión de unidades
- `test_parse_piece_type_*()` — parsing de códigos MiTek
- `test_parse_lumber_dims()` — extracción de dimensiones nominales
- `test_odoo_piece_to_ros_*()` — conversión completa de payload

---

## 🚀 Compilar y ejecutar

```bash
# Compilar
cd ~/ros2_ws
colcon build --packages-select ros2_odoo_bridge
source install/setup.bash

# Ejecutar
ros2 launch ros2_odoo_bridge bridge.launch.py

# Probar healthcheck
curl -s http://localhost:8000/health
# → {"status":"ok","node":"ros2_odoo_bridge"}
```

---

## 📦 Dependencias

### ROS2
- `rclpy`, `std_msgs`, `std_srvs`
- `geometry_msgs`, `shape_msgs`, `moveit_msgs`
- `rosidl_default_generators` (build)

### Python
- `requests`, `fastapi`, `uvicorn[standard]`, `pydantic`

## Compilar

```bash
cd ~/ros2_ws
colcon build --packages-select ros2_odoo_bridge
source install/setup.bash
```

## Configurar

Editar `config/params.yaml` con los datos reales:

```yaml
odoo_bridge:
  ros__parameters:
    odoo_url:      "https://odoo.tudominio.com"
    odoo_db:       "produccion"
    odoo_user:     "robot@tudominio.com"
    odoo_password: "CAMBIAR"
    ssl_cert:      "/etc/ssl/ros2pc/cert.pem"
    ssl_key:       "/etc/ssl/ros2pc/key.pem"
    server_port:   8443
```

## Ejecutar

```bash
ros2 launch ros2_odoo_bridge bridge.launch.py
```

## Topics

| Topic | Dirección | Tipo | Formato |
|---|---|---|---|
| `/robot/start_job` | Bridge → Robot | `std_msgs/String` | JSON con `production_id`, `cutting_list`, etc. |
| `/robot/job_completed` | Robot → Bridge | `std_msgs/String` | `"production_id"` |
| `/robot/qty_produced` | Robot → Bridge | `std_msgs/String` | `"production_id:qty"` |
| `/robot/job_error` | Robot → Bridge | `std_msgs/String` | `"production_id:mensaje"` |

## Endpoint HTTPS

El bridge expone un servidor HTTPS en el PC ROS2:

```
POST https://ros2pc.tudominio.com:8443/start_job
GET  https://ros2pc.tudominio.com:8443/health
GET  https://ros2pc.tudominio.com:8443/pending_jobs
```

### Payload de start_job (enviado desde Odoo)

```json
{
  "production_id": 42,
  "product_name": "Panel WF-001",
  "product_qty": 1.0,
  "cutting_list": [
    {"part": "top_plate", "length": "8-3-8", "qty": 2},
    {"part": "stud",      "length": "7-9-0", "qty": 12}
  ],
  "extra": {}
}
```

## Abrir puerto en el firewall

```bash
sudo ufw allow 8443/tcp
```
