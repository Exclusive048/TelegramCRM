from pydantic import ValidationInfo, field_validator
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
    yukassa_ip_whitelist: str = ""

    @field_validator(
        "master_admin_tg_id",
        "api_port",
        "sla_new_hours",
        "sla_in_progress_days",
        "subscription_price",
        "subscription_days",
        "trial_days",
        "referral_bonus_days",
        mode="before",
    )
    @classmethod
    def normalize_empty_numeric_env(cls, v, info: ValidationInfo):
        # Empty env values like MASTER_ADMIN_TG_ID="" should fall back to field default.
        if isinstance(v, str) and not v.strip():
            default = cls.model_fields[info.field_name].default
            if default is not None:
                return default
        return v

    @field_validator("public_domain")
    @classmethod
    def check_domain(cls, v: str) -> str:
        if not v or v.strip().upper() == "YOUR_DOMAIN":
            raise ValueError("Укажите реальный домен в .env (PUBLIC_DOMAIN=example.com)")
        return v.replace("https://", "").replace("http://", "").strip("/")


    @property
    def yukassa_ip_whitelist_set(self) -> set[str]:
        return {ip.strip() for ip in self.yukassa_ip_whitelist.split(",") if ip.strip()}


settings = Settings()
