from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/search.db"
    model_path: Path = Path("assets/model")
    api_key: SecretStr | None = None
    require_api_key: bool = False
    vercel: bool = False
    semantic_threshold: float = Field(default=0.60, ge=0, le=1)

    @model_validator(mode="after")
    def validate_api_key(self) -> "Settings":
        if (self.require_api_key or self.vercel) and (
            self.api_key is None or len(self.api_key.get_secret_value()) < 32
        ):
            raise ValueError("REQUIRE_API_KEY needs an API_KEY of at least 32 characters")
        if self.vercel and not self.database_url.startswith(("postgresql", "postgres://")):
            raise ValueError("Vercel requires a persistent PostgreSQL DATABASE_URL")
        return self
