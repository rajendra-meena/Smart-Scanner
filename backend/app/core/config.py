from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    kite_api_key: str = ""
    kite_api_secret: str = ""
    frontend_origin: str = "http://localhost:5173"
    session_cookie_secure: bool = False
    session_cookie_name: str = "smart_scanner_session"
    session_encryption_key: str = ""
    session_db_path: str = "./data/sessions.sqlite3"
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
