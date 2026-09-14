FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils && rm -rf /var/lib/apt/lists/*
COPY index.html /app/index.html
COPY tripy-icon.png /app/tripy-icon.png
COPY manifest.webmanifest /app/manifest.webmanifest
COPY telegram-bot/server.py /app/telegram-bot/server.py
RUN mkdir -p /app/trip-uploads
ENV PORT=8787
EXPOSE 8787
CMD ["python3", "/app/telegram-bot/server.py"]
