FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-heb && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pywebpush==2.1.2 webauthn==2.7.0
COPY index.html /app/index.html
COPY tripy-icon.png /app/tripy-icon.png
COPY manifest.webmanifest /app/manifest.webmanifest
COPY service-worker.js /app/service-worker.js
COPY telegram-bot/server.py /app/telegram-bot/server.py
COPY telegram-bot/email_rules.py /app/telegram-bot/email_rules.py
COPY telegram-bot/provider_rules.py /app/telegram-bot/provider_rules.py
COPY telegram-bot/booking_lifecycle.py /app/telegram-bot/booking_lifecycle.py
COPY telegram-bot/booking_types.py /app/telegram-bot/booking_types.py
COPY telegram-bot/booking_extraction.py /app/telegram-bot/booking_extraction.py
COPY telegram-bot/booking_facts.py /app/telegram-bot/booking_facts.py
COPY telegram-bot/semantic_hotel.py /app/telegram-bot/semantic_hotel.py
COPY telegram-bot/event_contract.py /app/telegram-bot/event_contract.py
COPY tools/audit_intake_corpus.py /app/tools/audit_intake_corpus.py
RUN mkdir -p /app/trip-uploads
ENV PORT=8787
EXPOSE 8787
CMD ["python3", "/app/telegram-bot/server.py"]
