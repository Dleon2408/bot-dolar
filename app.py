# ============================================================
#  SERVIDOR WHATSAPP (API oficial de Meta - Cloud API)
#  Recibe imagen + texto, edita el numero y responde la imagen.
# ============================================================

import os
import re
import tempfile

import requests
from fastapi import FastAPI, Request, Response

from editor import reemplazar_texto

# ---- Configuracion (se lee de variables de entorno en Render) ----
META_TOKEN = os.environ.get("META_TOKEN", "")          # Token de acceso de Meta
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")  # ID del numero de WhatsApp
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "fodie123")  # Lo inventas tu
GRAPH = "https://graph.facebook.com/v21.0"

app = FastAPI()


# ------------------------------------------------------------
#  1) Verificacion del webhook (Meta hace un GET al conectar)
# ------------------------------------------------------------
@app.get("/webhook")
async def verificar(request: Request):
    params = request.query_params
    if (params.get("hub.mode") == "subscribe" and
            params.get("hub.verify_token") == VERIFY_TOKEN):
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    return Response(content="Token invalido", status_code=403)


# ------------------------------------------------------------
#  2) Recepcion de mensajes (Meta hace POST cuando te escriben)
# ------------------------------------------------------------
@app.post("/webhook")
async def recibir(request: Request):
    data = await request.json()
    try:
        valor = data["entry"][0]["changes"][0]["value"]
        if "messages" not in valor:
            return {"ok": True}  # puede ser un "status", lo ignoramos
        mensaje = valor["messages"][0]
        de = mensaje["from"]  # numero del que escribe

        if mensaje.get("type") == "image":
            caption = mensaje["image"].get("caption", "")
            media_id = mensaje["image"]["id"]
            procesar_imagen(de, media_id, caption)
        elif mensaje.get("type") == "text":
            enviar_texto(
                de,
                "Mandame una *imagen* con un texto (caption) tipo:\n"
                "  cambia 3.410 por 3.400",
            )
    except Exception as e:
        print("Error procesando:", e)

    return {"ok": True}


# ------------------------------------------------------------
#  Logica principal: descargar, editar y responder
# ------------------------------------------------------------
def procesar_imagen(destino, media_id, caption):
    viejo, nuevo = parse_instruccion(caption)
    if not viejo or not nuevo:
        enviar_texto(
            destino,
            "No entendi el cambio. Escribe en el texto de la imagen algo como:\n"
            "  cambia 3.410 por 3.400",
        )
        return

    # 1) Descargar la imagen que mando el usuario
    entrada = descargar_media(media_id)
    salida = entrada.replace(".jpg", "_editada.jpg")

    # 2) Editar
    ok = reemplazar_texto(entrada, viejo, nuevo, salida)
    if not ok:
        enviar_texto(
            destino,
            f"No encontre '{viejo}' en la imagen. "
            f"Asegurate de escribirlo igual que aparece.",
        )
        return

    # 3) Responder con la imagen editada
    enviar_imagen(destino, salida)


def parse_instruccion(texto):
    """Extrae (valor_viejo, valor_nuevo) del texto del usuario."""
    if not texto:
        return None, None
    numeros = re.findall(r"\d+[.,]\d+|\d+", texto)
    if len(numeros) >= 2:
        return numeros[0], numeros[1]
    return None, None


# ------------------------------------------------------------
#  Llamadas a la API de Meta
# ------------------------------------------------------------
def _headers():
    return {"Authorization": f"Bearer {META_TOKEN}"}


def descargar_media(media_id):
    # a) pedir la URL del archivo
    r = requests.get(f"{GRAPH}/{media_id}", headers=_headers())
    url = r.json()["url"]
    # b) descargar el archivo
    img = requests.get(url, headers=_headers())
    ruta = os.path.join(tempfile.gettempdir(), f"{media_id}.jpg")
    with open(ruta, "wb") as f:
        f.write(img.content)
    return ruta


def subir_media(ruta):
    """Sube la imagen editada a Meta y devuelve su media_id."""
    with open(ruta, "rb") as f:
        archivos = {
            "file": ("imagen.jpg", f, "image/jpeg"),
            "messaging_product": (None, "whatsapp"),
            "type": (None, "image/jpeg"),
        }
        r = requests.post(
            f"{GRAPH}/{PHONE_NUMBER_ID}/media", headers=_headers(), files=archivos
        )
    return r.json()["id"]


def enviar_imagen(destino, ruta):
    media_id = subir_media(ruta)
    cuerpo = {
        "messaging_product": "whatsapp",
        "to": destino,
        "type": "image",
        "image": {"id": media_id},
    }
    requests.post(
        f"{GRAPH}/{PHONE_NUMBER_ID}/messages", headers=_headers(), json=cuerpo
    )


def enviar_texto(destino, texto):
    cuerpo = {
        "messaging_product": "whatsapp",
        "to": destino,
        "type": "text",
        "text": {"body": texto},
    }
    requests.post(
        f"{GRAPH}/{PHONE_NUMBER_ID}/messages", headers=_headers(), json=cuerpo
    )


# Pagina de inicio simple para comprobar que el servidor vive
@app.get("/")
async def inicio():
    return {"estado": "Bot del dolar funcionando"}


# Pagina de Politica de Privacidad (Meta la pide para publicar la app)
@app.get("/privacy")
async def privacidad():
    html = """
    <html><head><meta charset="utf-8"><title>Politica de Privacidad</title></head>
    <body style="font-family:Arial;max-width:700px;margin:40px auto;padding:0 20px;">
    <h1>Politica de Privacidad</h1>
    <p>Este bot de uso personal recibe imagenes y texto enviados por el
    usuario a traves de WhatsApp con el unico fin de editar la imagen
    solicitada y devolverla.</p>
    <ul>
      <li>No almacenamos las imagenes ni los mensajes de forma permanente.</li>
      <li>No compartimos ningun dato con terceros.</li>
      <li>Las imagenes se procesan temporalmente y se descartan.</li>
    </ul>
    <p>Para cualquier consulta, contacta al administrador del bot.</p>
    </body></html>
    """
    return Response(content=html, media_type="text/html")
