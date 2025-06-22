from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, validator
import requests
import time
from typing import Optional

app = FastAPI()

# Memoria temporal para sesiones de usuario: token, businessId y región
user_context = {}

# Modelos
class LoginData(BaseModel):
    usuario: str
    password: str
    region: str = "apidev"

class Producto(BaseModel):
    nombre: str
    precio: float
    costo: float | None = None
    moneda: str = Field(default="USD", description="Moneda del producto (USD, CUP o EUR)")
    tipo: str = Field(default="STOCK", description="Tipo de producto")
    categorias: list[str] = Field(default_factory=list)
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
    stockAreaId: Optional[int] = 0  # Permite omitir o usar 0 como consulta
    productos: list[ProductoEntradaInteligente]

# Helper para obtener la base URL
def get_base_url(region: str) -> str:
    region = region.lower().strip()
    if region == "apidev":
        return "https://apidev.tecopos.com"
    raise HTTPException(status_code=400, detail="Región inválida")

# Endpoint de login
@app.post("/login-tecopos")
def login_tecopos(data: LoginData):
    base_url = get_base_url(data.region)
    login_url = f"{base_url}/api/v1/security/login"
    userinfo_url = f"{base_url}/api/v1/security/user"

    payload = {
        "username": data.usuario,
        "password": data.password
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://admindev.tecopos.com",
        "Referer": "https://admindev.tecopos.com/",
        "x-app-origin": "Tecopos-Admin",
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.post(login_url, json=payload, headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    token = response.json().get("token")
    if not token:
        raise HTTPException(status_code=400, detail="Token no encontrado")

    headers_userinfo = headers.copy()
    headers_userinfo["Authorization"] = f"Bearer {token}"

    info_response = requests.get(userinfo_url, headers=headers_userinfo)
    if info_response.status_code != 200:
        raise HTTPException(status_code=400, detail="No se pudo obtener información del usuario")

    business_id = info_response.json().get("businessId")
    if not business_id:
        raise HTTPException(status_code=400, detail="No se encontró el businessId")

    user_context[data.usuario] = {
        "token": token,
        "businessId": business_id,
        "region": data.region
    }

    return {"status": "ok", "mensaje": "Login exitoso", "businessid": business_id}

@app.post("/entrada-inteligente")
def entrada_inteligente(data: EntradaInteligenteRequest):
    def normalizar(texto: str) -> str:
        return texto.strip().lower()

    context = user_context.get(data.usuario)
    if not context:
        raise HTTPException(status_code=403, detail="Usuario no autenticado")

    token = context["token"]
    businessid = context["businessId"]
    base_url = get_base_url(context["region"])

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://admindev.tecopos.com",
        "Referer": "https://admindev.tecopos.com/",
        "x-app-businessid": str(businessid),
        "x-app-origin": "Tecopos-Admin",
        "User-Agent": "Mozilla/5.0"
    }

    if not data.stockAreaId or data.stockAreaId == 0:
        stock_area_url = f"{base_url}/api/v1/administration/area?type=STOCK"
        res_almacenes = requests.get(stock_area_url, headers=headers)
        if res_almacenes.status_code != 200:
            raise HTTPException(status_code=500, detail="No se pudieron obtener los almacenes")

        stock_areas = res_almacenes.json().get("items", [])
        return {
            "status": "pendiente",
            "mensaje": "Seleccione un stockAreaId válido de la siguiente lista:",
            "almacenes": [
                {"id": a["id"], "nombre": a["name"]} for a in stock_areas
            ]
        }

    productos_a_insertar = []

    for producto in data.productos:
        nombre_normalizado = normalizar(producto.nombre)
        search_url = f"{base_url}/api/v1/administration/product?search={producto.nombre}"
        res_busqueda = requests.get(search_url, headers=headers)

        if res_busqueda.status_code != 200:
            raise HTTPException(status_code=500, detail=f"No se pudo buscar el producto '{producto.nombre}'")

        productos_encontrados = res_busqueda.json().get("items", [])
        existente = next((p for p in productos_encontrados if normalizar(p.get("name", "")) == nombre_normalizado), None)

        if existente:
            product_id = existente["id"]
        else:
            crear_url = f"{base_url}/api/v1/administration/product"
            crear_payload = {
                "type": "STOCK",
                "name": producto.nombre,
                "prices": [
                    {
                        "price": producto.precio,
                        "codeCurrency": producto.moneda
                    }
                ],
                "images": []
            }

            crear_res = requests.post(crear_url, headers=headers, json=crear_payload)
            if crear_res.status_code not in [200, 201]:
                raise HTTPException(status_code=500, detail=f"No se pudo crear el producto '{producto.nombre}'")

            product_id = crear_res.json().get("id")
            time.sleep(0.5)

        productos_a_insertar.append({
            "productId": product_id,
            "quantity": producto.cantidad
        })

    entrada_url = f"{base_url}/api/v1/administration/movement/bulk/entry"
    entrada_payload = {
        "products": productos_a_insertar,
        "stockAreaId": data.stockAreaId,
        "continue": False
    }

    entrada_res = requests.post(entrada_url, headers=headers, json=entrada_payload)
    if entrada_res.status_code not in [200, 201]:
        raise HTTPException(status_code=500, detail="No se pudo registrar la entrada")

    return {
        "status": "ok",
        "mensaje": f"Entrada realizada correctamente en stockAreaId {data.stockAreaId}",
        "productos_procesados": [p.nombre for p in data.productos]
    }


