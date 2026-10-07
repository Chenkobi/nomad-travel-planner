FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr && rm -rf /var/lib/apt/lists/*
COPY index.html /app/index.html
COPY tripy-icon.png /app/tripy-icon.png
COPY manifest.webmanifest /app/manifest.webmanifest
COPY telegram-bot/server.py /app/telegram-bot/server.py
COPY telegram-bot/email_rules.py /app/telegram-bot/email_rules.py
COPY telegram-bot/booking_lifecycle.py /app/telegram-bot/booking_lifecycle.py
COPY telegram-bot/booking_types.py /app/telegram-bot/booking_types.py
RUN mkdir -p /app/trip-uploads
ENV PORT=8787
EXPOSE 8787
CMD ["python3", "/app/telegram-bot/server.py"]
