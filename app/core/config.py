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

    tilda_secret: str = '' # Секрет для верификации запросов от Tilda, если не пустой

settings = Settings()
