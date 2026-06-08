from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    anthropic_api_key: str = Field(default="")
    # Single source of truth for the model string — never hardcode it elsewhere.
    llm_model: str = Field(default="claude-sonnet-4-20250514")

    # Broker
    groww_api_key: str = Field(default="")
    groww_api_secret: str = Field(default="")
    groww_access_token: str = Field(default="")

    # Feature flags
    live_trading_enabled: bool = Field(default=False)

    # Overrideable defaults (fall back to risk_limits.py if not set)
    max_trade_value_inr: float = Field(default=500.0)
    min_confidence: float = Field(default=0.80)

    @property
    def has_groww_credentials(self) -> bool:
        return all([self.groww_api_key, self.groww_api_secret, self.groww_access_token])

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def broker_mode(self) -> str:
        if self.live_trading_enabled and self.has_groww_credentials:
            return "live"
        return "paper"


settings = Settings()
