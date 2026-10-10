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


settings = Settings()
