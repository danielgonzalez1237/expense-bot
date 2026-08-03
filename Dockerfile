FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Tailwind CSS precompilado (sin dependencia del CDN runtime) ──
# El Play CDN (cdn.tailwindcss.com) es frágil/no-producción; lo compilamos
# acá con el CLI standalone (un binario, sin Node). Escanea static/index.html
# y emite static/tw.css con exactamente las clases usadas (incl. valores
# arbitrarios y dark:). Si esta descarga/compilación falla, el build falla y
# Railway mantiene el deploy anterior (no empeora prod).
ADD https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-linux-x64 /usr/local/bin/tailwindcss
RUN chmod +x /usr/local/bin/tailwindcss
COPY tailwind.config.js tailwind.input.css ./
COPY static/ ./static/
RUN /usr/local/bin/tailwindcss -c ./tailwind.config.js -i ./tailwind.input.css -o ./static/tw.css --minify \
    && test -s ./static/tw.css

COPY bot.py api.py ./
EXPOSE 8080
CMD ["python", "-u", "bot.py"]
