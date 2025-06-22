from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import requests

app = FastAPI()

# Memoria temporal para sesiones de usuario: token, businessId y región
user_context = {}

# Modelos
class LoginData(BaseModel):
    usuario: str
    password: str
    region: str = "apidev"  # Región por defecto para entorno de desarrollo

class Producto(BaseModel):
    nombre: str
    precio: float
    costo: float | None = None  # Costo opcional
    moneda: str = Field(default="USD", description="Moneda del producto (USD, CUP o EUR)")
    tipo: str = Field(default="STOCK", description="Tipo de producto")
    categorias: list[str] = Field(default_factory=list)
    usuario: str

class CambioMonedaRequest(BaseModel):
    usuario: str
    moneda_actual: str
    nueva_moneda: str

# Helper para construir la URL base según la región
def get_base_url(region: str) -> str:
    region = region.lower().strip()
    if region == "apidev":
        return "https://apidev.tecopos.com"
    raise HTTPException(status_code=400, detail="Región inválida (solo 'apidev' permitido en modo desarrollo)")

# Endpoint para autenticación y obtención de token y businessId
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
        raise HTTPException(status_code=400, detail="Token no encontrado en la respuesta")

    # Obtener información del usuario (incluye businessId)
    headers_userinfo = headers.copy()
    headers_userinfo["Authorization"] = f"Bearer {token}"

    info_response = requests.get(userinfo_url, headers=headers_userinfo)
    if info_response.status_code != 200:
        raise HTTPException(status_code=400, detail="No se pudo obtener información del usuario")

    business_id = info_response.json().get("businessId")
    if not business_id:
        raise HTTPException(status_code=400, detail="No se encontró el businessId del usuario")

    # Guardar sesión del usuario
    user_context[data.usuario] = {
        "token": token,
        "businessId": business_id,
        "region": data.region
    }

    return {"status": "ok", "mensaje": "Login exitoso", "businessid": business_id}

# Endpoint para crear producto
@app.post("/crear-producto")
def crear_producto(producto: Producto):
    context = user_context.get(producto.usuario)
    if not context:
        raise HTTPException(status_code=403, detail="Usuario no autenticado")

    token = context["token"]
    businessid = context["businessId"]
    base_url = get_base_url(context["region"])

    url = f"{base_url}/api/v1/administration/product"
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

    payload = {
        "type": producto.tipo,
        "name": producto.nombre,
        "prices": [
            {
                "price": producto.precio,
                "codeCurrency": producto.moneda
            }
        ],
        "images": []
    }

    if producto.costo is not None:
        payload["cost"] = producto.costo

    if producto.categorias:
        payload["categories"] = producto.categorias

    response = requests.post(url, json=payload, headers=headers)

    if response.status_code in [200, 201]:
        return {
            "status": "ok",
            "mensaje": "✅ Producto creado con éxito en Tecopos",
            "respuesta": response.json()
        }
    else:
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "error": "❌ No se pudo crear el producto",
                "respuesta": response.text
            }
        )

# Endpoint para cambiar moneda de precios existentes sin modificar el valor
@app.post("/actualizar-monedas")
def actualizar_monedas(data: CambioMonedaRequest):
    context = user_context.get(data.usuario)
    if not context:
        raise HTTPException(status_code=403, detail="Usuario no autenticado")

    token = context["token"]
    businessid = context["businessId"]
    base_url = get_base_url(context["region"])

    list_url = f"{base_url}/api/v1/administration/product"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "*/*",
        "Origin": "https://admindev.tecopos.com",
        "Referer": "https://admindev.tecopos.com/",
        "x-app-businessid": str(businessid),
        "x-app-origin": "Tecopos-Admin",
        "User-Agent": "Mozilla/5.0"
    }

    lista_productos = requests.get(list_url, headers=headers)
    if lista_productos.status_code != 200:
        raise HTTPException(status_code=500, detail="No se pudo obtener productos")

    productos = lista_productos.json().get("items", [])
    modificados = []

    for producto in productos:
        producto_id = producto.get("id")
        precios = producto.get("currencies", [])
        cambios = []

        for precio in precios:
            if precio.get("codeCurrency") == data.moneda_actual:
                cambios.append({
                    "systemPriceId": precio.get("systemPriceId"),
                    "price": precio.get("price"),
                    "codeCurrency": data.nueva_moneda
                })

        if cambios:
            patch_url = f"{base_url}/api/v1/administration/product/{producto_id}"
            patch_response = requests.patch(patch_url, json={"prices": cambios}, headers=headers)
            if patch_response.status_code in [200, 201]:
                modificados.append(producto["name"])

    return {
        "status": "ok",
        "mensaje": f"Monedas actualizadas para {len(modificados)} productos",
        "productos_actualizados": modificados
    }

