# TRIPY Telegram bridge

This is the working-mode bridge for the current static TRIPY app. It uses Telegram long polling and a small HTTP API. It intentionally has no hard-coded bot token.

## Run locally

1. Create a bot with `@BotFather` and keep its token private.
2. Start the bridge in the project directory:

```bash
TELEGRAM_BOT_TOKEN='YOUR_TOKEN_IN_SHELL_ONLY' python3 telegram-bot/server.py
```

Optional access restriction:

```bash
TELEGRAM_ALLOWED_CHAT_ID='YOUR_CHAT_ID' TELEGRAM_BOT_TOKEN='...' python3 telegram-bot/server.py
```

Open `http://127.0.0.1:8787/` to serve TRIPY. The bot accepts `/start`, `/today`, text updates containing טיסה/רכבת/מלון/אטרקציה, and documents/photos.

## Important limitation

GitHub Pages cannot run Python or receive Telegram webhooks. For the bot and app to work while the Mac is off, deploy this folder to an always-on service (Render, Railway, Fly.io, or a small VPS), then point the iPhone to that service URL. The next integration step is changing the frontend event loader from localStorage to `/api/events` while retaining local fallback.

Never commit the Telegram token or a chat export to GitHub.
