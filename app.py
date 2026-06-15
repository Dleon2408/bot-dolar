# ============================================================
#  SERVIDOR WHATSAPP (API oficial de Meta - Cloud API)
#  Recibe imagen + texto, edita el numero y responde la imagen.
#  Incluye flujo guiado tipo formulario y botones.
# ============================================================

import os
import re
import tempfile

import requests
from fastapi import FastAPI, Request, Response, BackgroundTasks

from editor import reemplazar_texto, reemplazar_varios, listar_numeros

# Memoria de mensajes ya procesados, para no responder dos veces
# si Meta reenvia el mismo mensaje (reintentos).
PROCESADOS = set()

# Memoria del "formulario" de cada usuario (en que paso va).
# de -> {"paso": "viejo"/"nuevo", "media_id": ..., "viejo": ...}
ESTADOS = {}

# Ultima imagen que mando cada usuario (para el boton "Corregir").
ULTIMA_IMAGEN = {}

# Palabras para reconocer saludos y despedidas
SALUDOS = {"hola", "holi", "buenas", "buenos", "hi", "hello", "ola", "hey", "alo"}
DESPEDIDAS = {"gracias", "adios", "chau", "chao", "listo", "ok", "oka", "bye"}

TEXTO_AYUDA = (
    "📖 *Cómo usarme:*\n"
    "1) Mándame una captura (imagen).\n"
    "2) Te pregunto qué número cambiar y por cuál.\n"
    "3) Te devuelvo la imagen editada.\n\n"
    "También puedes mandar la imagen con el texto:\n"
    "  cambia 3.410 por 3.400"
)

# ---- Configuracion (se lee de variables de entorno en Render) ----
META_TOKEN = os.environ.get("META_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "fodie123")
GRAPH = "https://graph.facebook.com/v21.0"
TIMEOUT = 30

# Lista blanca: SOLO estos numeros pueden usar el bot (vacio = todos).
PERMITIDOS = [
    n.strip() for n in os.environ.get("PERMITIDOS", "").split(",") if n.strip()
]

WABA_ID = os.environ.get("WABA_ID", "1039029152028883")

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
            return {"ok": True}
        mensaje = valor["messages"][0]

        # Evitar duplicados
        msg_id = mensaje.get("id")
        if msg_id in PROCESADOS:
            return {"ok": True}
        PROCESADOS.add(msg_id)
        if len(PROCESADOS) > 1000:
            PROCESADOS.clear()

        de = mensaje["from"]

        # Candado opcional (lista blanca)
        if PERMITIDOS and de not in PERMITIDOS:
            print("Numero no autorizado, ignorado:", de)
            return {"ok": True}

        tipo = mensaje.get("type")

        if tipo == "image":
            media_id = mensaje["image"]["id"]
            ULTIMA_IMAGEN[de] = media_id
            caption = mensaje["image"].get("caption", "")
            pares = parse_pares(caption)
            if pares:
                # Camino rapido: vino todo en el texto (1 o varios cambios)
                ESTADOS.pop(de, None)
                background.add_task(procesar_edicion, de, media_id, pares)
            else:
                # Inicia el formulario guiado
                ESTADOS[de] = {"paso": "viejo", "media_id": media_id}
                background.add_task(
                    enviar_texto, de,
                    "📷 ¡Recibí tu imagen!\n\n"
                    "¿Qué número quieres cambiar?\n"
                    "Escríbelo tal como aparece (ej: 3.410)",
                )

        elif tipo == "interactive":
            br = mensaje["interactive"].get("button_reply", {})
            manejar_texto(de, br.get("id", ""), background)

        elif tipo == "text":
            manejar_texto(de, mensaje["text"]["body"], background)

    except Exception as e:
        print("Error procesando:", e)

    return {"ok": True}


# ------------------------------------------------------------
#  Logica del formulario / conversacion
# ------------------------------------------------------------
def manejar_texto(de, texto, background):
    t = texto.strip().lower()
    palabras = set(re.findall(r"\w+", t))
    estado = ESTADOS.get(de)

    # --- Botones ---
    if texto == "editar_otra":
        ESTADOS.pop(de, None)
        background.add_task(
            enviar_texto, de, "📷 ¡Genial! Mándame la siguiente imagen."
        )
        return
    if texto == "terminar":
        ESTADOS.pop(de, None)
        background.add_task(
            enviar_texto, de, "🙌 ¡Listo! Aquí estaré cuando necesites. 👋"
        )
        return
    if texto == "corregir":
        # Reutiliza la ultima imagen para corregir sin reenviarla
        media_id = ULTIMA_IMAGEN.get(de)
        if media_id:
            ESTADOS[de] = {"paso": "viejo", "media_id": media_id}
            background.add_task(
                enviar_texto, de,
                "✏️ De acuerdo. ¿Qué número corrijo? (escríbelo como aparece)",
            )
        else:
            background.add_task(enviar_texto, de, "📷 Mándame la imagen otra vez.")
        return
    if "cancelar" in palabras:
        ESTADOS.pop(de, None)
        background.add_task(
            enviar_texto, de, "❌ Cancelado. Mándame una imagen cuando quieras."
        )
        return
    if texto == "ayuda" or "ayuda" in palabras or "menu" in palabras:
        background.add_task(enviar_texto, de, TEXTO_AYUDA)
        return

    # --- En medio del formulario ---
    if estado:
        num = primer_numero(texto)
        if not num:
            background.add_task(
                enviar_texto, de,
                "Escríbeme solo el número, por ejemplo: 3.410\n"
                "(o escribe *cancelar* para salir)",
            )
            return
        if estado["paso"] == "viejo":
            estado["viejo"] = num
            estado["paso"] = "nuevo"
            background.add_task(
                enviar_texto, de, f"✏️ Cambiar *{num}* por... ¿cuál número?"
            )
            return
        if estado["paso"] == "nuevo":
            media_id = estado["media_id"]
            viejo = estado["viejo"]
            ESTADOS.pop(de, None)
            background.add_task(procesar_edicion, de, media_id, [(viejo, num)])
            return

    # --- Sin formulario activo: saludo / despedida / otro ---
    if palabras & SALUDOS:
        background.add_task(
            enviar_texto, de,
            "👋 ¡Hola! Soy tu bot editor de imágenes.\n"
            "Mándame una captura y te ayudo a cambiar un número. 📷",
        )
        return
    if palabras & DESPEDIDAS:
        background.add_task(
            enviar_texto, de, "🙌 ¡De nada! Aquí estaré cuando necesites. 👋"
        )
        return
    background.add_task(
        enviar_texto, de,
        "📷 Mándame una *imagen* y te pregunto qué número cambiar.",
    )


def procesar_edicion(destino, media_id, pares):
    """pares = lista de (viejo, nuevo). Edita, responde y limpia."""
    entrada = salida = None
    try:
        enviar_texto(destino, "✏️ Editando tu imagen, dame unos segundos...")
        entrada = descargar_media(media_id)
        salida = entrada.replace(".jpg", "_editada.jpg")

        resultados = reemplazar_varios(entrada, pares, salida)
        hechos = [v for v, ok in resultados if ok]
        fallidos = [v for v, ok in resultados if not ok]

        if not hechos:
            # No se logro ningun cambio: mostrar los numeros detectados
            nums = listar_numeros(entrada)
            if nums:
                enviar_texto(
                    destino,
                    f"😕 No encontré {', '.join(fallidos)} en la imagen.\n\n"
                    f"Los números que veo son:\n  {'   '.join(nums)}\n\n"
                    f"Toca *Corregir* y dime el número correcto.",
                )
                enviar_botones(
                    destino, "👇",
                    [("corregir", "✏️ Corregir"), ("terminar", "🏁 Terminar")],
                )
            else:
                enviar_texto(
                    destino,
                    f"😕 No encontré {', '.join(fallidos)}. Escríbelo tal como aparece.",
                )
            return

        # Hubo al menos un cambio: enviamos la imagen
        enviar_imagen(destino, salida)
        aviso = "✅ ¿Quedó bien? Si no, toca *Corregir*."
        if fallidos:
            aviso = (
                f"⚠️ Cambié {', '.join(hechos)}, pero no encontré "
                f"{', '.join(fallidos)}.\n¿Quieres corregir?"
            )
        enviar_botones(
            destino, aviso,
            [("corregir", "✏️ Corregir"), ("terminar", "🏁 Terminar")],
        )
    except Exception as e:
        print("Error editando:", e)
        enviar_texto(
            destino, "⚠️ Ocurrió un error al editar. Intenta de nuevo en un momento."
        )
    finally:
        # Limpieza de archivos temporales
        for ruta in (entrada, salida):
            try:
                if ruta and os.path.exists(ruta):
                    os.remove(ruta)
            except OSError:
                pass


def parse_pares(texto):
    """
    Extrae una lista de pares (viejo, nuevo) del texto.
    Toma los numeros en orden y los empareja: 1o-2o, 3o-4o, ...
    Ej: 'cambia 3.410 por 3.400 y 3.375 por 3.380'
        -> [('3.410','3.400'), ('3.375','3.380')]
    """
    if not texto:
        return []
    numeros = re.findall(r"\d+[.,]\d+|\d+", texto)
    pares = []
    for i in range(0, len(numeros) - 1, 2):
        pares.append((numeros[i], numeros[i + 1]))
    return pares


def primer_numero(texto):
    """Devuelve el primer numero que aparezca en el texto, o None."""
    m = re.findall(r"\d+[.,]\d+|\d+", texto or "")
    return m[0] if m else None


# ------------------------------------------------------------
#  Llamadas a la API de Meta
# ------------------------------------------------------------
def _headers():
    return {"Authorization": f"Bearer {META_TOKEN}"}


def descargar_media(media_id):
    r = requests.get(f"{GRAPH}/{media_id}", headers=_headers(), timeout=TIMEOUT)
    url = r.json()["url"]
    img = requests.get(url, headers=_headers(), timeout=TIMEOUT)
    ruta = os.path.join(tempfile.gettempdir(), f"{media_id}.jpg")
    with open(ruta, "wb") as f:
        f.write(img.content)
    return ruta


def subir_media(ruta):
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


def enviar_botones(destino, texto, botones):
    """botones = lista de (id, titulo). Maximo 3 botones, titulo <= 20 letras."""
    cuerpo = {
        "messaging_product": "whatsapp",
        "to": destino,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": texto},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": bid, "title": bt}}
                    for bid, bt in botones
                ]
            },
        },
    }
    requests.post(
        f"{GRAPH}/{PHONE_NUMBER_ID}/messages",
        headers=_headers(),
        json=cuerpo,
        timeout=TIMEOUT,
    )


# ------------------------------------------------------------
#  Paginas auxiliares
# ------------------------------------------------------------
# Acepta GET y HEAD para que monitores como UptimeRobot no den falso 405
@app.api_route("/", methods=["GET", "HEAD"])
async def inicio():
    return {"estado": "Bot del dolar funcionando"}


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
