from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    discord_bot_token: str
    source_channel_ids: str = ""
    destination_channel_id: int
    database_url: str = "sqlite+aiosqlite:///tasks.db"
    task_threshold: int = 70
    high_confidence_threshold: int = 90

    # AI classification (Ollama Cloud OpenAI-compatible by default)
    ai_enabled: bool = True
    ai_api_key: str = ""
    ai_base_url: str = "https://ollama.com/v1"
    ai_model: str = "gemma4:31b"
    ai_prefilter_threshold: int = 25
    ai_timeout_seconds: float = 30.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def source_channels(self) -> set[int]:
        result = set()
        for value in self.source_channel_ids.split(","):
            value = value.strip()
            if value:
                result.add(int(value))
        return result

    @property
    def ai_ready(self) -> bool:
        return self.ai_enabled and bool(self.ai_api_key.strip())

@lru_cache
def get_settings() -> Settings:
    return Settings()
