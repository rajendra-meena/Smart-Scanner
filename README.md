# Smart Scanner

A modern Zerodha Kite Connect workspace with a React dashboard and FastAPI live market-data backend.

## Local setup (Windows PowerShell)

### 1. Backend

```powershell
cd backend
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `KITE_API_KEY`, `KITE_API_SECRET`, `FRONTEND_ORIGIN=http://127.0.0.1:5173`, and `SESSION_COOKIE_SECURE=false` in `backend/.env`. After installing requirements, generate a persistent encryption key and place it in `SESSION_ENCRYPTION_KEY`:\n\n```powershell\npython -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\n```\n\nKeep this key private and unchanged across restarts. The encrypted SQLite session database is stored at `backend/data/sessions.sqlite3` by default (relative to the backend working directory). Back up the database and the encryption key together; losing or changing the key makes existing stored sessions unreadable. In the Kite developer console, register this exact redirect URL: `http://127.0.0.1:8000/api/auth/zerodha/callback`.

Start the API:

```powershell
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend

Open another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open **http://127.0.0.1:5173** (use this hostname consistently for the browser session). Vite proxies `/api` HTTP and WebSocket requests to FastAPI on port 8000.

## Authentication flow

1. The Connect button asks the backend for the official Kite login URL.
2. Zerodha redirects to the registered backend callback with a short-lived `request_token`.
3. The backend exchanges it using the official `kiteconnect` SDK and API secret.
4. The access token stays server-side. The browser receives only an opaque HTTP-only session cookie.
5. The local session expires at 06:00 Asia/Kolkata on the next day.
6. Logout clears the local session, closes the market WebSocket, and calls Kite's access-token invalidation endpoint.

## Live market data

- Uses the official Python `KiteTicker` WebSocket client; market ticks are forwarded to the browser through an authenticated FastAPI WebSocket.
- The instrument master is fetched through Kite Connect and filtered to NSE cash equities, nearest-expiry NFO futures, and selected NSE indices.
- Equity and futures gainers/losers are ranked against the previous close in the streamed quote packets.
- Advance/decline currently represents the subscribed NSE cash-equity universe for which ticks have been received in the current session. It is not an official NIFTY index constituent breadth calculation.
- The app caps subscriptions at 3,000 instruments per Kite WebSocket connection.\n- Ticks with a zero/missing LTP or previous close are excluded from rankings and breadth; they are counted in diagnostics instead of being shown as -100% moves.\n- The UI distinguishes a connected WebSocket from fresh ticks. A connected socket alone is not represented as proof that the market is currently streaming.
- Index quotes are shown when received. Weighted index contributor points are intentionally not calculated yet: verified constituent weights are not provided by the Kite instrument master and must be integrated from a reliable official index constituent source before displaying contribution rankings.
- Live ticks arrive during exchange trading; outside market hours the connection may be established but no fresh tick stream should be expected.

## Development limitations and security

- Zerodha sessions are persisted in SQLite; access tokens are encrypted with Fernet using `SESSION_ENCRYPTION_KEY`. Keep the encryption key private and stable across restarts, and back it up securely with the database. The live market feed remains a single-process in-memory service; use a managed shared feed lifecycle before deploying multiple workers or production.
- Set `SESSION_COOKIE_SECURE=true` behind HTTPS in production.
- Keep `.env` private; never commit API credentials or access tokens.
- This project currently displays market data only. It does not place orders or execute trading strategies.

## API endpoints

- `GET /api/health`
- `GET /api/auth/zerodha/login`
- `GET /api/auth/zerodha/callback`
- `GET /api/auth/session`
- `POST /api/auth/logout`
- `GET /api/market/snapshot`
- `GET /api/market/status`
- `WS /api/market/stream`
