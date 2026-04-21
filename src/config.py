"""Runtime configuration with pydantic-settings.

All values come from env vars (with .env file fallback). Validators fail closed —
out-of-bounds risk caps raise before the app can mis-trade.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- Broker ---
    ALPACA_API_KEY: str = Field(default="")
    ALPACA_SECRET_KEY: str = Field(default="")
    ALPACA_PAPER_TRADE: bool = Field(default=True)

    # --- Infra ---
    POSTGRES_DSN: str = Field(
        default="postgresql+asyncpg://trader:trader@localhost:5432/algotrader"
    )
    POSTGRES_DSN_SYNC: str = Field(
        default="postgresql+psycopg2://trader:trader@localhost:5432/algotrader"
    )
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # --- Risk caps (hard ceilings; validators forbid going above v1 defaults) ---
    MAX_PER_TRADE_RISK: Decimal = Field(default=Decimal("0.01"))
    MAX_PORTFOLIO_HEAT: Decimal = Field(default=Decimal("0.06"))
    MAX_SINGLE_POSITION: Decimal = Field(default=Decimal("0.10"))
    DAILY_LOSS_HALT: Decimal = Field(default=Decimal("-0.02"))
    DRAWDOWN_HALT: Decimal = Field(default=Decimal("0.15"))

    # --- Dashboard ---
    DASHBOARD_API_HOST: str = Field(default="0.0.0.0")
    DASHBOARD_API_PORT: int = Field(default=8000)
    DASHBOARD_WEB_PORT: int = Field(default=3000)

    # --- Live trading guard (string so env "0"/"1" round-trips) ---
    LIVE_TRADING: Literal["0", "1"] = Field(default="0")

    # --- Logging ---
    LOG_LEVEL: str = Field(default="INFO")

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def journal_dir(self) -> Path:
        return PROJECT_ROOT / "journal"

    @property
    def backtests_dir(self) -> Path:
        return PROJECT_ROOT / "backtests"

    @property
    def live_dir(self) -> Path:
        return PROJECT_ROOT / "live"

    # Validators reject any cap that would let v1 trade more aggressively than the
    # design allows. Loosening these requires editing the file by hand.
    @field_validator("MAX_PER_TRADE_RISK")
    @classmethod
    def _per_trade_risk_bounds(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v <= Decimal("0.01")):
            raise ValueError("MAX_PER_TRADE_RISK must be in (0, 0.01]")
        return v

    @field_validator("MAX_PORTFOLIO_HEAT")
    @classmethod
    def _portfolio_heat_bounds(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v <= Decimal("0.06")):
            raise ValueError("MAX_PORTFOLIO_HEAT must be in (0, 0.06]")
        return v

    @field_validator("MAX_SINGLE_POSITION")
    @classmethod
    def _single_position_bounds(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v <= Decimal("0.10")):
            raise ValueError("MAX_SINGLE_POSITION must be in (0, 0.10]")
        return v

    @field_validator("DAILY_LOSS_HALT")
    @classmethod
    def _daily_loss_bounds(cls, v: Decimal) -> Decimal:
        if not (Decimal("-0.10") <= v < Decimal("0")):
            raise ValueError("DAILY_LOSS_HALT must be in [-0.10, 0)")
        return v

    @field_validator("DRAWDOWN_HALT")
    @classmethod
    def _drawdown_bounds(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v <= Decimal("0.30")):
            raise ValueError("DRAWDOWN_HALT must be in (0, 0.30]")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance. Tests call `get_settings.cache_clear()` to reset."""
    return Settings()
