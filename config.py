# ============================================================
#  CONFIGURACION DEL BOT
#  Detecta solo si corre en tu PC (Windows) o en la nube (Linux).
# ============================================================

import os
import platform

ES_WINDOWS = platform.system() == "Windows"

# Ruta al programa Tesseract (el motor de OCR).
if ES_WINDOWS:
    # En tu PC, donde lo instalaste
    TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
else:
    # En la nube (Render/Linux) viene en el PATH
    TESSERACT_PATH = "tesseract"

# Fuente: usamos el archivo 'fuente.ttf' que va DENTRO del proyecto,
# asi se ve igual en tu PC y en la nube. (Es una copia de Arial.)
FONT_PATH = os.path.join(os.path.dirname(__file__), "fuente.ttf")

# Desenfoque leve del numero nuevo para igualar la "calidad" de la captura.
# 0 = nitido total. 0.5-0.9 = como una captura comprimida.
TEXT_BLUR = 0.6
