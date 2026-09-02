# SICM – IPL MOCK AUCTION

## Supabase
Project: `sicm-ipl-mock-auction`
Region: `ap-south-1`
URL: `https://edsgttjugmindpuqisec.supabase.co`

## Local setup
1. Copy `.env.example` to `.env`.
2. Put the Supabase **service role key** in `SUPABASE_SERVICE_ROLE_KEY` (never commit it).
3. Set `SECRET_KEY` and `ADMIN_PASSWORD`.
4. `pip install -r requirements.txt`
5. `python app.py`

## Demo flow
Home → Register team → Login → private dashboard.
Admin login → add players → set player live → physical bidding → enter team + price → Mark Sold.

The sale is performed by a PostgreSQL transaction/function, enforcing wallet, squad size, duplicate-sale and idempotency rules.
