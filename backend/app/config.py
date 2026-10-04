from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Resolve the project configuration independently of the shell's working directory.
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    database_url: str | None = None
    mausam_database_url: str | None = None
    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    allowed_origins: str = "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:5175,http://localhost:5175"
    open_meteo_seasonal_url: str = "https://seasonal-api.open-meteo.com/v1/seasonal"
    open_meteo_customer_url: str | None = None
    weatherapi_api_key: str | None = None
    enable_open_meteo_ingestion: bool = False
    enable_public_climate_ingestion: bool = False
    copernicus_cds_url: str | None = None
    copernicus_cds_key: str | None = None
    sms_provider: str | None = None
    sms_provider_api_key: str | None = None
    sms_provider_sender_id: str | None = None
    sms_provider_template_id: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_messaging_service_sid: str | None = None
    enable_notification_dispatch: bool = False
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    redis_url: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.8-flash"
    gemini_tts_model: str = "gemini-3.8-flash-lite-tts"

    @model_validator(mode="after")
    def choose_database_url(self):
        # Compose uses DATABASE_URL; local host runs may use the documented override.
        if not self.database_url and self.mausam_database_url:
            self.database_url = self.mausam_database_url
        return self

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


settings = Settings()
