# Smart Scanner

A modern Zerodha Kite Connect workspace with a React dashboard and FastAPI authentication backend.

## Local setup (Windows PowerShell)

### 1. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `KITE_API_KEY`, `KITE_API_SECRET`, `FRONTEND_ORIGIN`, and `SESSION_COOKIE_SECURE` in `backend/.env`. In the Kite developer console, register this exact redirect URL: `http://127.0.0.1:8000/api/auth/zerodha/callback`.

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

Open http://localhost:5173. Vite proxies `/api` requests to FastAPI on port 8000.

## Authentication flow

1. The Connect button asks the backend for the official Kite login URL.
2. Zerodha redirects to the registered backend callback with a short-lived `request_token`.
3. The backend exchanges it using the official `kiteconnect` SDK and API secret.
4. The access token stays server-side. The browser receives only an opaque HTTP-only session cookie.
5. The local session expires at the same time as the Kite access token: 06:00 Asia/Kolkata on the next day.
6. Logout clears the local session and calls Kite's access-token invalidation endpoint.

## Development limitations and security

- The in-memory session store is intended only for single-process local development. Use Redis or a database before deploying with multiple workers or in production.
- Set `SESSION_COOKIE_SECURE=true` behind HTTPS in production.
- Keep `.env` private; never commit API credentials.
- This milestone implements authentication only. No order placement or trading controls are included.

## API endpoints

- `GET /api/health`
- `GET /api/auth/zerodha/login`
- `GET /api/auth/zerodha/callback`
- `GET /api/auth/session`
- `POST /api/auth/logout`
