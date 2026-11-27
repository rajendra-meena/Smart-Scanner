from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import auth, health, market, strategies, price_action
from app.core.config import settings

app = FastAPI(
    title="Smart Scanner API",
    version="0.2.0",
    description="Smart Scanner authentication and live market data service.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(market.router, prefix="/api")
app.include_router(strategies.router, prefix="/api")
app.include_router(price_action.router, prefix="/api")


@app.get("/")
def root():
    return {"message": "Smart Scanner API"}
