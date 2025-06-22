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

# Crear producto
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

# Actualizar monedas
class CambioMonedaRequest(BaseModel):
    usuario: str
    moneda_actual: str
    nueva_moneda: str
    confirmar: bool = False
    forzar_todos: bool = False  # Nuevo parámetro


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

    response = requests.get(list_url, headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="No se pudo obtener productos")

    productos = response.json().get("items", [])
    productos_para_actualizar = []

    for producto in productos:
        producto_id = producto.get("id")
        nombre = producto.get("name")
        precios = producto.get("prices", [])

        # Si hay más de un precio y no se forzó, se omite el producto
        if len(precios) != 1 and not data.forzar_todos:
            continue

        cambios = []

        for precio in precios:
            price_system_id = precio.get("priceSystemId")
            moneda_actual = precio.get("codeCurrency")
            monto = precio.get("price")

            if moneda_actual == data.moneda_actual and price_system_id is not None:
                cambios.append({
                    "systemPriceId": str(price_system_id),
                    "price": monto,
                    "codeCurrency": data.nueva_moneda
                })

        if cambios:
            productos_para_actualizar.append({
                "id": producto_id,
                "nombre": nombre,
                "cambios": cambios
            })

    if not data.confirmar:
        return {
            "status": "pendiente",
            "mensaje": f"Se encontraron {len(productos_para_actualizar)} productos con moneda {data.moneda_actual}.",
            "productos_para_cambiar": productos_para_actualizar
        }

    productos_modificados = []

    for p in productos_para_actualizar:
        patch_url = f"{base_url}/api/v1/administration/product/{p['id']}"
        patch_payload = {
            "prices": p["cambios"]
        }

        patch_response = requests.patch(patch_url, json=patch_payload, headers=headers)

        if patch_response.status_code in [200, 201]:
            productos_modificados.append(p["nombre"])
        else:
            print(f"⚠️ Error modificando '{p['nombre']}': {patch_response.status_code} - {patch_response.text}")

    return {
        "status": "ok",
        "mensaje": f"Se actualizó la moneda en {len(productos_modificados)} productos.",
        "productos_actualizados": productos_modificados
    }
