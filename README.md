# Lumaqix — Render + PostgreSQL starter

1. Push this folder to GitHub.
2. Create a Render Web Service from the repo.
3. Create a Render PostgreSQL database.
4. Add environment variables from `.env.example`.
5. Set `WEBAPP_URL` to the deployed Render URL.
6. Put the Telegram BotFather token in `BOT_TOKEN`.
7. Keep private keys out of GitHub and chat.

This package is the server/database foundation. The blockchain watcher and real BEP20 transaction signer should be added only after Testnet wallet/RPC details are configured. The `/api/withdraw` endpoint currently queues a withdrawal; it does NOT broadcast a real blockchain transaction.
