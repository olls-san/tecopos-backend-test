from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, validator
from typing import Optional, List
import requests
import time

app = FastAPI()
user_context = {}

# --------- MODELOS ---------
class LoginData(BaseModel):
    usuario: str
    password: str
    region: str = "apidev"

class Producto(BaseModel):
    nombre: str
    precio: float
    costo: float | None = None
    moneda: str = Field(default="USD")
    tipo: str = Field(default="STOCK")
    categorias: List[str] = Field(default_factory=list)
    usuario: str

class CambioMonedaRequest(BaseModel):
    usuario: str
    moneda_actual: str
    nueva_moneda: str
    confirmar: bool = False
    forzar_todos: bool = False

class ProductoEntradaInteligente(BaseModel):
    nombre: str
    cantidad: int
    precio: float
    moneda: str = "CUP"

    @validator("cantidad")
    def validar_cantidad_positiva(cls, v):
        if v <= 0:
            raise ValueError("La cantidad debe ser mayor que cero")
        return v

    @validator("nombre")
    def validar_nombre_no_vacio(cls, v):
        if not v.strip():
            raise ValueError("El nombre del producto no puede estar vacío")
        return v

class EntradaInteligenteRequest(BaseModel):
    usuario: str
    stockAreaId: Optional[int] = 0
    productos: List[ProductoEntradaInteligente]

# --------- HELPERS ---------
def get_base_url(region: str) -> str:
    region = region.lower().strip()
    if region == "apidev":
        return "https://apidev.tecopos.com"
    raise HTTPException(status_code=400, detail="Región inválida")

def get_auth_headers(token: str, businessid: int) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://admindev.tecopos.com",
        "Referer": "https://admindev.tecopos.com/",
        "x-app-businessid": str(businessid),
        "x-app-origin": "Tecopos-Admin",
        "User-Agent": "Mozilla/5.0"
    }

def normalizar(texto: str) -> str:
    return texto.strip().lower()

def inferir_categoria(nombre: str) -> str:
    nombre = normalizar(nombre)
    if any(palabra in nombre for palabra in ["cerveza", "ron", "vino"]):
        return "Bebidas Alcohólicas"
    if any(palabra in nombre for palabra in ["refresco", "soda", "jugos"]):
        return "Refrescos"
    return "Mercado"

def obtener_o_crear_categoria(nombre_categoria: str, base_url: str, headers: dict) -> int:
    cat_url = f"{base_url}/api/v1/administration/salescategory"
    res = requests.get(cat_url, headers=headers)
    if res.status_code != 200:
        raise HTTPException(status_code=500, detail="No se pudieron consultar las categorías")

    categorias = res.json().get("items", [])
    existente = next((c for c in categorias if normalizar(c.get("name", "")) == normalizar(nombre_categoria)), None)
    if existente:
        return existente["id"]

    crear_res = requests.post(cat_url, headers=headers, json={"name": nombre_categoria})
    if crear_res.status_code not in [200, 201]:
        raise HTTPException(status_code=500, detail="No se pudo crear la categoría")

    return crear_res.json().get("id")

def buscar_o_crear_producto(producto: ProductoEntradaInteligente, base_url: str, headers: dict) -> int:
    nombre_norm = normalizar(producto.nombre)
    search_url = f"{base_url}/api/v1/administration/product?search={producto.nombre}"
    res = requests.get(search_url, headers=headers)

    if res.status_code != 200:
        raise HTTPException(status_code=500, detail=f"No se pudo buscar '{producto.nombre}'")

    items = res.json().get("items", [])
    existente = next((p for p in items if normalizar(p.get("name", "")) == nombre_norm), None)

    if existente:
        return existente["id"]

    categoria_id = obtener_o_crear_categoria(inferir_categoria(producto.nombre), base_url, headers)

    crear_url = f"{base_url}/api/v1/administration/product"
    crear_payload = {
        "type": "STOCK",
        "name": producto.nombre,
        "prices": [{"price": producto.precio, "codeCurrency": producto.moneda, "systemPriceId": 1}],
        "images": [],
        "salesCategoryId": categoria_id
    }

    crear_res = requests.post(crear_url, headers=headers, json=crear_payload)
    if crear_res.status_code not in [200, 201]:
        raise HTTPException(status_code=500, detail=f"No se pudo crear '{producto.nombre}'")

    time.sleep(0.5)
    return crear_res.json().get("id")

# --------- ENDPOINTS ---------
@app.post("/login-tecopos")
def login_tecopos(data: LoginData):
    base_url = get_base_url(data.region)
    login_url = f"{base_url}/api/v1/security/login"
    userinfo_url = f"{base_url}/api/v1/security/user"

    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://admindev.tecopos.com",
        "Referer": "https://admindev.tecopos.com/",
        "x-app-origin": "Tecopos-Admin",
        "User-Agent": "Mozilla/5.0"
    }

    res = requests.post(login_url, json={"username": data.usuario, "password": data.password}, headers=headers)
    if res.status_code != 200:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    token = res.json().get("token")
    headers["Authorization"] = f"Bearer {token}"

    info = requests.get(userinfo_url, headers=headers)
    businessid = info.json().get("businessId")

    if not token or not businessid:
        raise HTTPException(status_code=400, detail="No se pudo obtener token o businessId")

    user_context[data.usuario] = {
        "token": token,
        "businessId": businessid,
        "region": data.region
    }

    return {"status": "ok", "mensaje": "Login exitoso", "businessid": businessid}

@app.post("/crear-producto-con-categoria")
def crear_producto_con_categoria(data: Producto):
    ctx = user_context.get(data.usuario)
    if not ctx:
        raise HTTPException(status_code=403, detail="Usuario no autenticado")

    base_url = get_base_url(ctx["region"])
    headers = get_auth_headers(ctx["token"], ctx["businessId"])

    categoria_nombre = data.categorias[0] if data.categorias else inferir_categoria(data.nombre)
    categoria_id = obtener_o_crear_categoria(categoria_nombre, base_url, headers)

    crear_payload = {
        "type": data.tipo,
        "name": data.nombre,
        "prices": [
            {
                "price": data.precio,
                "codeCurrency": data.moneda,
                "systemPriceId": 1
            }
        ],
        "images": [],
        "salesCategoryId": categoria_id
    }

    crear_url = f"{base_url}/api/v1/administration/product"
    crear_res = requests.post(crear_url, headers=headers, json=crear_payload)
    if crear_res.status_code not in [200, 201]:
        raise HTTPException(status_code=500, detail="No se pudo crear el producto")

    return {
        "status": "ok",
        "mensaje": f"Producto '{data.nombre}' creado en categoría '{categoria_nombre}'",
        "respuesta": crear_res.json()
    }

@app.post("/entrada-inteligente")
def entrada_inteligente(data: EntradaInteligenteRequest):
    ctx = user_context.get(data.usuario)
    if not ctx:
        raise HTTPException(status_code=403, detail="Usuario no autenticado")

    base_url = get_base_url(ctx["region"])
    headers = get_auth_headers(ctx["token"], ctx["businessId"])

    if not data.stockAreaId:
        almacenes_url = f"{base_url}/api/v1/administration/area?type=STOCK"
        res = requests.get(almacenes_url, headers=headers)
        if res.status_code != 200:
            raise HTTPException(status_code=500, detail="No se pudieron obtener los almacenes")

        almacenes = res.json().get("items", [])
        return {
            "status": "ok",
            "mensaje": "Seleccione un área de stock",
            "almacenes": [{"id": a["id"], "nombre": a["name"]} for a in almacenes]
        }

    procesados = []
    for prod in data.productos:
        producto_id = buscar_o_crear_producto(prod, base_url, headers)
        entrada_url = f"{base_url}/api/v1/stock/product-entry"
        entrada_payload = {
            "products": [
                {"productId": producto_id, "quantity": prod.cantidad}
            ],
            "stockAreaId": data.stockAreaId,
            "continue": False
        }
        entrada_res = requests.post(entrada_url, headers=headers, json=entrada_payload)
        if entrada_res.status_code not in [200, 201]:
            raise HTTPException(status_code=500, detail=f"No se pudo dar entrada a '{prod.nombre}'")
        procesados.append(prod.nombre)

    return {
        "status": "ok",
        "mensaje": "Productos procesados correctamente",
        "productos_procesados": procesados
    }

@app.post("/actualizar-monedas")
def actualizar_monedas(data: CambioMonedaRequest):
    ctx = user_context.get(data.usuario)
    if not ctx:
        raise HTTPException(status_code=403, detail="Usuario no autenticado")

    base_url = get_base_url(ctx["region"])
    headers = get_auth_headers(ctx["token"], ctx["businessId"])

    productos_url = f"{base_url}/api/v1/administration/product"
    res = requests.get(productos_url, headers=headers)

    if res.status_code != 200:
        raise HTTPException(status_code=500, detail="Error al obtener productos")

    productos = res.json().get("items", [])
    pendientes = []

    for p in productos:
        precios = p.get("prices", [])
        for precio in precios:
            if precio.get("codeCurrency") == data.moneda_actual:
                pendientes.append({
                    "id": p["id"],
                    "nombre": p["name"],
                    "price": precio["price"],
                    "systemPriceId": precio.get("systemPriceId", 1)
                })

    if not data.confirmar:
        return {
            "status": "ok",
            "mensaje": "Simulación de cambio de moneda",
            "productos_para_cambiar": pendientes
        }

    actualizados = []
    for prod in pendientes:
        patch_url = f"{base_url}/api/v1/administration/product/{prod['id']}"
        patch_payload = {
            "prices": [
                {
                    "systemPriceId": prod["systemPriceId"],
                    "price": prod["price"],
                    "codeCurrency": data.nueva_moneda
                }
            ]
        }
        patch_res = requests.patch(patch_url, headers=headers, json=patch_payload)
        if patch_res.status_code in [200, 204]:
            actualizados.append(prod["nombre"])

    return {
        "status": "ok",
        "mensaje": "Monedas actualizadas correctamente",
        "productos_actualizados": actualizados
    }

