from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import auth, health
from app.core.config import settings

app = FastAPI(title="Smart Scanner API", version="0.1.0",
              description="Zerodha Kite Connect authentication service.")
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin],
                   allow_credentials=True, allow_methods=["GET","POST","OPTIONS"],
                   allow_headers=["Content-Type"])
app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")

@app.get("/")
def root():
    return {"message":"Smart Scanner API"}
