FROM python:3.12-slim
WORKDIR /app
COPY index.html /app/index.html
COPY telegram-bot/server.py /app/telegram-bot/server.py
RUN mkdir -p /app/trip-uploads
ENV PORT=8787
EXPOSE 8787
CMD ["python3", "/app/telegram-bot/server.py"]
