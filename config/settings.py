from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM — tiered model routing. Single source of truth for model strings.
    anthropic_api_key: str = Field(default="")
    # Cheap, fast default for the research agents (scout/fundamental/technical/reflection).
    llm_model_default: str = Field(default="claude-haiku-4-5-20251001")
    # Stronger model reserved for the capital-protecting judge.
    llm_model_judge: str = Field(default="claude-sonnet-4-6")

    # Broker
    groww_api_key: str = Field(default="")
    groww_api_secret: str = Field(default="")
    groww_access_token: str = Field(default="")

    # Reddit (optional — social sentiment on stocks; read-only public posts)
    reddit_client_id: str = Field(default="")
    reddit_client_secret: str = Field(default="")

    # Finnhub + NewsAPI (optional, free — richer news/insider/earnings data)
    finnhub_api_key: str = Field(default="")
    news_api_key: str = Field(default="")

    # Dashboard (read-only web view, secured by a secret token in the URL path)
    dashboard_secret_token: str = Field(default="")
    dashboard_owner_name: str = Field(default="Trader")
    dashboard_port: int = Field(default=8765)

    # Telegram (optional — remote trade approval from your phone)
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")
    auto_approve_timeout_seconds: int = Field(default=1800)  # 30 min, then auto-reject
    auto_approve_if_no_telegram: bool = Field(default=True)  # paper mode convenience

    # Feature flags
    live_trading_enabled: bool = Field(default=False)
    paper_demo_mode: bool = Field(default=False)

    # Trading mode — the system's risk dial. conserve | balanced | rogue.
    # Overridable at runtime via the /conserve /balanced /rogue Telegram commands.
    trading_mode: str = Field(default="balanced")

    # Overrideable defaults (fall back to risk_limits.py if not set)
    max_trade_value_inr: float = Field(default=500.0)
    min_confidence: float = Field(default=0.80)

    # Paper/demo starting capital (INR) — realistic Indian retail swing account.
    # Root-cause fix: the old ₹1500 default couldn't size even 1 share of most
    # large/mid-cap stocks within the 3% max-risk-per-trade limit (e.g. 1 share
    # of a ₹1040 stock with a 7% stop risks ₹72 > ₹45 = 3% of ₹1500), so every
    # real pipeline trade was rejected before sizing. Live mode NEVER uses this —
    # live capital always comes from the broker (see RiskChecker/live executor).
    paper_capital_inr: float = Field(default=100000.0)

    @property
    def has_groww_credentials(self) -> bool:
        return all([self.groww_api_key, self.groww_api_secret, self.groww_access_token])

    @property
    def telegram_chat_id_int(self) -> int | None:
        """Chat id as an int, or None if unset/non-numeric. Telegram chat ids are
        integers; storing as str keeps Pydantic happy, this parses on demand."""
        try:
            return int(self.telegram_chat_id) if self.telegram_chat_id else None
        except ValueError:
            return None

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id_int is not None)

    @property
    def has_finnhub(self) -> bool:
        return bool(self.finnhub_api_key)

    @property
    def has_dashboard_token(self) -> bool:
        return bool(self.dashboard_secret_token)

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def broker_mode(self) -> str:
        if self.live_trading_enabled and self.has_groww_credentials:
            return "live"
        return "paper"

    @property
    def effective_demo_mode(self) -> bool:
        if self.live_trading_enabled:
            return False
        return self.paper_demo_mode or not self.has_anthropic_key

    @property
    def mode_label(self) -> str:
        if self.live_trading_enabled:
            return "🔴 LIVE"
        if self.effective_demo_mode:
            return "🧪 DEMO"
        return "📝 PAPER"


settings = Settings()
