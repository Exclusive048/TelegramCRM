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

    # ── Топики (заполняются после /setup) ─────────────────────
    topic_new:          int = 2
    topic_in_progress:  int = 4
    topic_paid:         int = 6
    topic_success:      int = 8
    topic_rejected:     int = 10
    topic_general:      int = 12
    topic_reminders:    int = 14
    topic_cabinet:      int = 16
    topic_managers:     int = 18
    topic_knowledge:    int = 20

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
