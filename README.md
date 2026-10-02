# Lumaqix — Render Ready

A Telegram Mini App + bot starter.

## Features
- Telegram Mini App
- Telegram initData validation
- Daily +100 points
- Referral +500 points
- Leaderboard
- SQLite database
- FastAPI backend
- Render deployment configuration

## Required Render environment variables

`BOT_TOKEN`
- Your Telegram BotFather token.
- Never publish this token in GitHub.

`WEBAPP_URL`
- Your Render HTTPS service URL, for example:
  `https://lumaqix.onrender.com`

## Render

Build command:
`pip install -r requirements.txt`

Start command:
`python app/main.py`

Health check:
`/health`

## Important

This starter uses points only. It does not process real-money deposits, withdrawals, investments, or guaranteed profits.
