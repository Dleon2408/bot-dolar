# ============================================================
#  SERVIDOR WHATSAPP (API oficial de Meta - Cloud API)
#  Recibe imagen + texto, edita el numero y responde la imagen.
# ============================================================

import os
import re
import tempfile

import requests
from fastapi import FastAPI, Request, Response, BackgroundTasks

from editor import reemplazar_texto

# Memoria de mensajes ya procesados, para no responder dos veces
# si Meta reenvia el mismo mensaje (reintentos).
PROCESADOS = set()

# ---- Configuracion (se lee de variables de entorno en Render) ----
META_TOKEN = os.environ.get("META_TOKEN", "")          # Token de acceso de Meta
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")  # ID del numero de WhatsApp
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "fodie123")  # Lo inventas tu
GRAPH = "https://graph.facebook.com/v21.0"
TIMEOUT = 30  # segundos maximos de espera en cada llamada a Meta

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
async def recibir(request: Request, background: BackgroundTasks):
    data = await request.json()
    try:
        valor = data["entry"][0]["changes"][0]["value"]
        if "messages" not in valor:
            return {"ok": True}  # puede ser un "status", lo ignoramos
        mensaje = valor["messages"][0]

        # Evitar duplicados: si ya vimos este mensaje, lo ignoramos
        msg_id = mensaje.get("id")
        if msg_id in PROCESADOS:
            return {"ok": True}
        PROCESADOS.add(msg_id)
        if len(PROCESADOS) > 1000:      # no dejar que crezca infinito
            PROCESADOS.clear()

        de = mensaje["from"]  # numero del que escribe

        # Procesamos en SEGUNDO PLANO para responderle a Meta al instante
        # (asi no reenvia el mensaje y no llegan imagenes repetidas).
        if mensaje.get("type") == "image":
            caption = mensaje["image"].get("caption", "")
            media_id = mensaje["image"]["id"]
            background.add_task(procesar_imagen, de, media_id, caption)
        elif mensaje.get("type") == "text":
            background.add_task(
                enviar_texto,
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
            "🤔 No entendi el cambio.\n\n"
            "Manda la imagen y en el texto escribe que cambiar, por ejemplo:\n"
            "  cambia 3.410 por 3.400",
        )
        return

    try:
        # Aviso rapido para que sepas que estamos trabajando
        enviar_texto(destino, "✏️ Editando tu imagen, dame unos segundos...")

        # 1) Descargar la imagen que mando el usuario
        entrada = descargar_media(media_id)
        salida = entrada.replace(".jpg", "_editada.jpg")

        # 2) Editar
        ok = reemplazar_texto(entrada, viejo, nuevo, salida)
        if not ok:
            enviar_texto(
                destino,
                f"😕 No encontre '{viejo}' en la imagen.\n"
                f"Escribelo IGUAL a como aparece (ej: 3.410).",
            )
            return

        # 3) Responder con la imagen editada
        enviar_imagen(destino, salida)
    except Exception as e:
        print("Error editando:", e)
        enviar_texto(
            destino,
            "⚠️ Ocurrio un error al editar. Intenta de nuevo en un momento.",
        )


def parse_instruccion(texto):
    """
    Extrae (valor_viejo, valor_nuevo) del texto del usuario.
    Toma los dos primeros numeros que aparezcan, en orden.
    Ej: 'cambia 3.410 por 3.400' -> ('3.410', '3.400')
    """
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
    r = requests.get(f"{GRAPH}/{media_id}", headers=_headers(), timeout=TIMEOUT)
    url = r.json()["url"]
    # b) descargar el archivo
    img = requests.get(url, headers=_headers(), timeout=TIMEOUT)
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
            f"{GRAPH}/{PHONE_NUMBER_ID}/media",
            headers=_headers(),
            files=archivos,
            timeout=TIMEOUT,
        )
    datos = r.json()
    if "id" not in datos:
        raise RuntimeError(f"Meta no devolvio media id: {datos}")
    return datos["id"]


def enviar_imagen(destino, ruta):
    media_id = subir_media(ruta)
    cuerpo = {
        "messaging_product": "whatsapp",
        "to": destino,
        "type": "image",
        "image": {"id": media_id},
    }
    requests.post(
        f"{GRAPH}/{PHONE_NUMBER_ID}/messages",
        headers=_headers(),
        json=cuerpo,
        timeout=TIMEOUT,
    )


def enviar_texto(destino, texto):
    cuerpo = {
        "messaging_product": "whatsapp",
        "to": destino,
        "type": "text",
        "text": {"body": texto},
    }
    requests.post(
        f"{GRAPH}/{PHONE_NUMBER_ID}/messages",
        headers=_headers(),
        json=cuerpo,
        timeout=TIMEOUT,
    )


# Pagina de inicio simple para comprobar que el servidor vive
@app.get("/")
async def inicio():
    return {"estado": "Bot del dolar funcionando"}


# ID de tu cuenta de WhatsApp Business (lo vimos en la configuracion)
WABA_ID = os.environ.get("WABA_ID", "1039029152028883")


# Pagina "secreta" para enganchar la app a tu cuenta de WhatsApp.
# Abrela UNA vez en el navegador despues de desplegar.
@app.get("/activar")
async def activar():
    try:
        r = requests.post(
            f"{GRAPH}/{WABA_ID}/subscribed_apps",
            headers=_headers(),
            timeout=TIMEOUT,
        )
        return {"resultado": r.json()}
    except Exception as e:
        return {"error": str(e)}


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
