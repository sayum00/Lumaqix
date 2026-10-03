# Lumaqix Complete

Telegram Bot + Mini App with PostgreSQL, NOWPayments deposits/IPN, automatic payouts when NOWPAYMENTS_JWT is configured, withdrawals, referrals, random daily rewards, statistics, transaction history and admin APIs.

## Environment
Use the existing Render variables and add/confirm:
- DATABASE_URL
- BOT_TOKEN
- BOT_USERNAME
- WEBAPP_URL
- ADMIN_IDS
- NOWPAYMENTS_API_KEY
- NOWPAYMENTS_IPN_SECRET
- NOWPAYMENTS_JWT (for automatic payouts)

Default daily reward is random between $1 and $5. Change DAILY_REWARD_MIN_USD and DAILY_REWARD_MAX_USD.

Do not commit secrets. NOWPayments payout access may need to be enabled/configured in the NOWPayments account.

## Deploy
Upload all files to the existing `sayum00/Lumaqix` repository, commit to `main`, then manually deploy the latest commit in Render because Auto Deploy is currently disabled.
