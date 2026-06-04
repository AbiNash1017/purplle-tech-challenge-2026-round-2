from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    MONGO_URI: str = Field(default="mongodb://localhost:27017/store_intelligence")
    # Upstash Redis REST credentials (no raw TCP needed)
    UPSTASH_REDIS_REST_URL: str = Field(default="")
    UPSTASH_REDIS_REST_TOKEN: str = Field(default="")
    EXECUTION_MODE: str = Field(default="SIMULATED")
    API_URL: str = Field(default="http://localhost:8000")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
