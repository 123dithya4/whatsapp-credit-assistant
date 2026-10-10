from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    twilio_auth_token: str = ""
    public_base_url: str = ""
    twilio_validate_signature: bool = True
    twilio_account_sid: str = ""
    whisper_model: str = "whisper-large-v3"
    whisper_language: str = ""  # empty = auto-detect; set e.g. "hi" to force Hindi
    whisper_allowed_languages: str = "english,en,hindi,hi,malayalam,ml,kannada,kn,tamil,ta"
    whisper_fallback_language: str = "ml"
    twilio_whatsapp_from: str = "whatsapp:+14155238886"
    owner_whatsapp: str = ""
    reminders_enabled: bool = False
    reminder_hour: int = 9
    reminder_minute: int = 0
    reminder_after_days: int = 7


settings = Settings()
