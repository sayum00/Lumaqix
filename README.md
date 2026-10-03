# Lumaqix — updated bot backend + Mini App

The uploaded project contained only `main.py` and `requirements.txt`; there was no original `web/` UI. This package therefore includes a small replacement Mini App UI and a backend payment/referral layer.

## Included
- Telegram `/start` + Mini App
- User accounts and referral links
- Balance and transaction ledger
- NOWPayments USDT/BSC deposit creation
- NOWPayments IPN HMAC-SHA512 verification and idempotent balance credit
- Configurable referral commission
- Withdrawal request with atomic balance reservation
- Optional NOWPayments payout call when `NOWPAYMENTS_JWT` is configured
- Payout callback handling with refund-on-failure
- Basic admin overview endpoint
- Transaction/referral/statistics UI

## Render variables
Copy `.env.example` values into Render Environment Variables. Never commit `.env`, API keys, IPN secrets, bot tokens, JWTs, wallet private keys, or seed phrases.

For NOWPayments, the API key authenticates outgoing API requests and the IPN Secret Key verifies callbacks. The official docs show `POST /v1/payment` for payment creation and IPN callbacks for payment status updates. The payout API requires its own payout setup/authorization. See the official NOWPayments documentation: https://nowpayments.io/api

## Important production note
The database is SQLite. For a serious production payment service, move the ledger to PostgreSQL and add stronger admin authentication, rate limits, audit logging, and reconciliation before handling significant funds.
