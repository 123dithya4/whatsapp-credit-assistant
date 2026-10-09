from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    twilio_auth_token: str = ""
    public_base_url: str = ""
    twilio_validate_signature: bool = True


settings = Settings()
