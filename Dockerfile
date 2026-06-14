# Imagen base con Python
FROM python:3.12-slim

# Instalar Tesseract (el motor de OCR) en la nube
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar librerias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del proyecto (codigo + fuente.ttf)
COPY . .

# Arrancar el servidor web (Render entrega el puerto en la variable PORT)
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
