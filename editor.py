# ============================================================
#  EDITOR DE IMAGEN
#  Encuentra un texto/numero en la imagen y lo reemplaza por
#  otro, manteniendo color de fondo, color de letra, tamano y
#  posicion. Pensado para capturas limpias (fondo plano).
# ============================================================

from collections import Counter
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import pytesseract

import config

# Le decimos a pytesseract donde esta el programa Tesseract
pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_PATH


def _normalizar(texto):
    """Quita espacios para comparar '3.410' aunque venga con ruido."""
    return texto.replace(" ", "").strip()


def _buscar_texto(img, valor_viejo):
    """
    Usa OCR para ubicar 'valor_viejo' en la imagen.
    Devuelve (left, top, width, height) o None si no lo encuentra.
    """
    objetivo = _normalizar(valor_viejo)
    datos = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

    mejor = None
    mejor_conf = -1
    for i in range(len(datos["text"])):
        palabra = _normalizar(datos["text"][i])
        if not palabra:
            continue
        if palabra == objetivo or objetivo in palabra:
            try:
                conf = float(datos["conf"][i])
            except ValueError:
                conf = 0
            if conf > mejor_conf:
                mejor_conf = conf
                mejor = (
                    datos["left"][i],
                    datos["top"][i],
                    datos["width"][i],
                    datos["height"][i],
                )
    return mejor


def _region_ampliada(img, caja):
    """Recorte alrededor de la caja, ampliado para abarcar toda la tinta."""
    left, top, width, height = caja
    ex = max(4, height)            # margen vertical (arriba/abajo)
    mx = max(2, height // 3)       # margen horizontal (poco, para no tocar vecinos)
    x0 = max(0, left - mx)
    y0 = max(0, top - ex)
    x1 = min(img.width, left + width + mx)
    y1 = min(img.height, top + height + ex)
    return img.crop((x0, y0, x1, y1)).convert("RGB"), x0, y0


def _color_de_fondo(region):
    """El fondo es el color MAS comun de la region (los digitos son minoria)."""
    return Counter(region.getdata()).most_common(1)[0][0]


def _color_de_letra(region, color_fondo):
    """La tinta = color mas comun entre los pixeles bien distintos del fondo."""
    fr, fg, fb = color_fondo
    candidatos = [
        p for p in region.getdata()
        if abs(p[0] - fr) + abs(p[1] - fg) + abs(p[2] - fb) > 150
    ]
    if not candidatos:
        return (0, 0, 0)
    return Counter(candidatos).most_common(1)[0][0]


def _extent_real(region, x_off, y_off, caja, color_fondo):
    """
    Mide la extension REAL (en pixeles) de la tinta del numero, aislando
    SOLO la banda continua del numero (crece desde el centro hasta una
    fila vacia, para no mezclarse con el texto de arriba o abajo).
    Devuelve (left, top, width, height) absolutos en la imagen.
    """
    fr, fg, fb = color_fondo
    ancho, alto = region.size
    px = region.load()

    def fila_tiene_tinta(y):
        for x in range(ancho):
            r, g, b = px[x, y]
            if abs(r - fr) + abs(g - fg) + abs(b - fb) > 150:
                return True
        return False

    # Fila semilla = centro de la caja del OCR dentro de la region
    _, top0, _, h0 = caja
    seed = (top0 - y_off) + h0 // 2
    seed = max(0, min(alto - 1, seed))
    if not fila_tiene_tinta(seed):
        encontrada = False
        for dy in range(1, alto):
            for s in (seed - dy, seed + dy):
                if 0 <= s < alto and fila_tiene_tinta(s):
                    seed, encontrada = s, True
                    break
            if encontrada:
                break
        if not encontrada:
            return caja

    # Crecemos hacia arriba y hacia abajo mientras haya tinta continua
    top_r = seed
    while top_r - 1 >= 0 and fila_tiene_tinta(top_r - 1):
        top_r -= 1
    bot_r = seed
    while bot_r + 1 < alto and fila_tiene_tinta(bot_r + 1):
        bot_r += 1

    # Columnas con tinta dentro de esa banda
    left_c, right_c = ancho, -1
    for y in range(top_r, bot_r + 1):
        for x in range(ancho):
            r, g, b = px[x, y]
            if abs(r - fr) + abs(g - fg) + abs(b - fb) > 150:
                left_c = min(left_c, x)
                right_c = max(right_c, x)
    if right_c < 0:
        return caja

    left = x_off + left_c
    top = y_off + top_r
    width = right_c - left_c + 1
    height = bot_r - top_r + 1
    return (left, top, width, height)


def _fuente_por_alto(texto, alto_objetivo):
    """Elige el tamano de fuente cuya tinta tenga ~alto_objetivo px de alto."""
    size = max(8, alto_objetivo)
    for _ in range(120):
        f = ImageFont.truetype(config.FONT_PATH, size)
        bb = f.getbbox(texto)
        alto_tinta = bb[3] - bb[1]
        if alto_tinta < alto_objetivo:
            size += 1
        elif alto_tinta > alto_objetivo:
            return ImageFont.truetype(config.FONT_PATH, max(8, size - 1))
        else:
            return f
    return ImageFont.truetype(config.FONT_PATH, size)


def reemplazar_texto(ruta_imagen, valor_viejo, valor_nuevo, ruta_salida):
    """
    Funcion principal. Reemplaza valor_viejo por valor_nuevo.
    Devuelve True si lo logro, False si no encontro el texto.
    """
    img = Image.open(ruta_imagen).convert("RGB")

    caja_ocr = _buscar_texto(img, valor_viejo)
    if caja_ocr is None:
        return False

    # 1) Analizamos colores en una region ampliada
    region, x_off, y_off = _region_ampliada(img, caja_ocr)
    color_fondo = _color_de_fondo(region)
    color_letra = _color_de_letra(region, color_fondo)

    # 2) Medimos la extension REAL de la tinta (no la caja chica del OCR)
    left, top, width, height = _extent_real(
        region, x_off, y_off, caja_ocr, color_fondo
    )

    dibujo = ImageDraw.Draw(img)

    # 3) Tapamos el numero viejo (con un pelin de margen) con el color de fondo
    pad = 2
    dibujo.rectangle(
        [left - pad, top - pad, left + width + pad, top + height + pad],
        fill=color_fondo,
    )

    # 4) Calculamos posicion del numero nuevo (mismo alto y posicion)
    fuente = _fuente_por_alto(valor_nuevo, height)
    bb = dibujo.textbbox((0, 0), valor_nuevo, font=fuente)
    ancho_texto = bb[2] - bb[0]
    centro_x = left + width / 2
    pos_x = centro_x - ancho_texto / 2 - bb[0]
    pos_y = top - bb[1]  # alinea el tope de la tinta con el tope real

    # 5) Dibujamos el texto en una capa aparte, le aplicamos un leve
    #    desenfoque para igualar la calidad de la captura, y lo fusionamos.
    capa = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(capa).text(
        (pos_x, pos_y), valor_nuevo, font=fuente, fill=color_letra + (255,)
    )
    if getattr(config, "TEXT_BLUR", 0):
        capa = capa.filter(ImageFilter.GaussianBlur(config.TEXT_BLUR))

    base = img.convert("RGBA")
    base.alpha_composite(capa)
    base.convert("RGB").save(ruta_salida)
    return True
