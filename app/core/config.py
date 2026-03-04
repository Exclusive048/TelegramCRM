from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str
    master_bot_token: str = ""
    crm_bot_username: str = "crm_bot"
    master_admin_tg_id: int = 0
    database_url: str
    use_redis: bool = False
    redis_url: str = "redis://localhost:6379/3"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    public_domain: str
    acme_email: str = ""
    sla_new_hours: int = 2
    sla_in_progress_days: int = 3
    subscription_price: int = 990
    subscription_days: int = 30
    trial_days: int = 14
    referral_bonus_days: int = 14
    support_username: str = "@support"
    yukassa_shop_id: str = ""
    yukassa_secret_key: str = ""

    @field_validator("public_domain")
    @classmethod
    def check_domain(cls, v: str) -> str:
        if not v or v.strip().upper() == "YOUR_DOMAIN":
            raise ValueError("Укажите реальный домен в .env (PUBLIC_DOMAIN=example.com)")
        return v.replace("https://", "").replace("http://", "").strip("/")


settings = Settings()
