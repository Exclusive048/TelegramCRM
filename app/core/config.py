from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── ОБЯЗАТЕЛЬНЫЕ (нет дефолта — без них не запустится) ────
    bot_token:       str
    crm_group_id:    int
    database_url:    str
    api_secret_key:  str   # никогда не хардкодить! только через .env
    public_domain:  str = "YOUR_DOMAIN"  # example.com without scheme

    @field_validator("public_domain")
    @classmethod
    def check_domain(cls, v: str) -> str:  # FIXED #10
        if not v or v.strip().upper() == "YOUR_DOMAIN":
            raise ValueError("Укажите реальный домен в .env (PUBLIC_DOMAIN=example.com)")
        return v.replace("https://", "").replace("http://", "").strip("/")  # FIXED #10

    # ── Redis ──────────────────────────────────────────────────
    use_redis:  bool = False          # false = MemoryStorage (для dev)
    redis_url:  str  = "redis://localhost:6379/3"

    # ── API сервер ─────────────────────────────────────────────
    api_host:   str = "0.0.0.0"
    api_port:   int = 8000

    # ── SLA ────────────────────────────────────────────────────
    sla_new_hours:         int = 2
    sla_in_progress_days:  int = 3

    # ── Email ──────────────────────────────────────────────────
    default_export_email: str = ""

    # ── Google Sheets ──────────────────────────────────────────
    google_service_account_file: str = "scripts/google_service_account.json"


settings = Settings()
