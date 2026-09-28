"""Application configuration (Pydantic Settings)."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, List, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    APP_NAME: str = "ORB AI"
    APP_VERSION: str = "1.2.3"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = True

    # ---- Server ----
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ---- Database ----
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://orb:orb@localhost:5432/orb_ai",
        description="SQLAlchemy async database URL.",
    )

    # ---- Security ----
    JWT_SECRET_KEY: str = Field(default="dev-insecure-secret-change-me", min_length=16)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    BCRYPT_ROUNDS: int = 12

    # ---- Encryption (Fernet) — used from Module 3 onwards ----
    ENCRYPTION_KEY: str | None = Field(
        default=None,
        description=(
            "Fernet key (44 char urlsafe base64). Required from Module 3. "
            "Generate: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ),
    )

    # ---- CORS ----
    # ``NoDecode`` prevents pydantic-settings from JSON-decoding the raw env
    # value so the ``_split_origins`` validator below can accept a plain
    # comma-separated string (e.g. "https://orb-ai.co.in,https://www.orb-ai.co.in").
    # Without it, a non-JSON env value raises JSONDecodeError at startup.
    CORS_ORIGINS: Annotated[List[str], NoDecode] = Field(default_factory=lambda: ["*"])

    # ---- Logging ----
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_JSON: bool = True

    # ---- Redis (optional) ----
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_ENABLED: bool = False

    # ---- Trading engine ----
    MARKET_DATA_PROVIDER: str = "mock"
    # ---- Dhan REST market data (optional; used when MARKET_DATA_PROVIDER=dhan) ----
    # Same credential shape as the Dhan broker adapter / WS provider. When
    # both are present AND MARKET_DATA_PROVIDER=dhan, the REST /candles and
    # /tick endpoints fetch genuine Dhan data and label source="dhan".
    DHAN_CLIENT_ID: str | None = None
    DHAN_ACCESS_TOKEN: str | None = None
    # ---- Upstox Market Data Feed V3 (optional; MARKET_DATA_PROVIDER=upstox) ----
    # First-class live market-data provider alongside dhan / kotak_neo / mock.
    # UPSTOX_ENABLED is an explicit feature flag; UPSTOX_ACCESS_TOKEN is the
    # daily V3 access token. Both are optional and default off so existing
    # deployments are unaffected. The token is NEVER logged or exposed to the
    # frontend. When MARKET_DATA_PROVIDER=upstox the provider fails closed
    # (raises) if no credential is present — it never silently serves mock data.
    UPSTOX_ENABLED: bool = False
    UPSTOX_ACCESS_TOKEN: str | None = None
    # ---- Upstox Analytics Token (READ-ONLY market data ONLY) ----
    # Upstox "Analytics" tokens grant read-only access to Market Quote,
    # Historical Data and the market-data WebSocket — but NOT to order
    # placement/modification/cancellation. This is a compatibility fallback:
    # when UPSTOX_ACCESS_TOKEN is absent, the read-only market-data provider
    # may authenticate with UPSTOX_ANALYTICS_TOKEN instead. It is NEVER used
    # for order execution (enforced in app.engine.market_data.upstox_credentials),
    # never logged, never returned via the API and never persisted.
    # Resolution priority is always: UPSTOX_ACCESS_TOKEN → UPSTOX_ANALYTICS_TOKEN.
    UPSTOX_ANALYTICS_TOKEN: str | None = None
    MOCK_TICK_INTERVAL_MS: int = Field(default=250, ge=10, le=60_000)
    MOCK_RANDOM_SEED: int = 42
    TRADING_SESSION_START: str = "00:00"  # HH:MM in TRADING_SESSION_TIMEZONE
    TRADING_SESSION_END: str = "23:59"
    TRADING_SESSION_TIMEZONE: str = "Asia/Kolkata"

    # ---- Module 8: Notifications ----
    # Email — Resend
    RESEND_API_KEY: str | None = None
    RESEND_FROM_EMAIL: str = "ORB AI <noreply@orb-ai.local>"

    # Email — SMTP (fallback)
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str | None = None
    SMTP_USE_TLS: bool = True

    # Telegram
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None

    # Emergent-managed push (mobile builds only)
    EMERGENT_PUSH_ENABLED: bool = False
    EMERGENT_PUSH_ENDPOINT: str | None = None
    EMERGENT_PUSH_API_KEY: str | None = None

    # Notification dispatch behaviour
    NOTIFICATIONS_ASYNC: bool = True
    NOTIFICATIONS_MAX_RETRIES: int = 3

    # ---- Module 8: Reports ----
    REPORTS_MAX_ROWS: int = 50_000
    REPORTS_COMPANY_NAME: str = "ORB AI"
    REPORTS_COMPANY_TAGLINE: str = "Algorithmic Trading Platform"

    # ---- Module 8: Analytics cache ----
    ANALYTICS_CACHE_TTL_SECONDS: int = 30

    # ---- Module 8: Scheduler ----
    SCHEDULER_ENABLED: bool = False
    SCHEDULER_TIMEZONE: str = "UTC"
    SCHEDULER_SNAPSHOT_CRON: str = "0 18 * * *"          # daily 18:00 UTC
    SCHEDULER_WEEKLY_DIGEST_CRON: str = "0 6 * * MON"    # Monday 06:00 UTC
    SCHEDULER_NOTIFICATION_RETRY_CRON: str = "*/5 * * * *"

    # ---- Trading Master Switch ----
    # Post-arm cooldown (seconds) during which LIVE order execution is
    # rejected server-side even though the mode is LIVE.  Set to 0 to
    # disable (tests / paper-only installations).  Enforced by
    # TradingModeService.assert_live_enabled() on every LIVE order.
    TRADING_MODE_LIVE_COOLDOWN_SECONDS: int = 60
    # Cron for the auto-PAPER-revert scheduler tick.  Actual trigger time
    # is admin-configurable and persisted in the DB — this cron only
    # controls how often the scheduler checks.  Set to empty string to
    # disable registration entirely.
    TRADING_MODE_AUTO_REVERT_CRON: str = "*/5 * * * *"

    # ---- Module 8: Subscriptions & billing ----
    # Supported providers: "noop" (default, v1.0.0 legacy — free tier
    # auto-activation), "mock" (dev + tests full lifecycle), "razorpay"
    # (primary, INR), "stripe" (secondary, USD).
    BILLING_PROVIDER: str = "noop"
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None
    STRIPE_SUCCESS_URL: str = "https://orb-ai.local/billing/success"
    STRIPE_CANCEL_URL: str = "https://orb-ai.local/billing/cancel"
    # ---- Razorpay ----
    RAZORPAY_KEY_ID: str | None = None
    RAZORPAY_KEY_SECRET: str | None = None
    RAZORPAY_WEBHOOK_SECRET: str | None = None
    RAZORPAY_SUCCESS_URL: str = "https://orb-ai.local/billing/success"
    RAZORPAY_CANCEL_URL: str = "https://orb-ai.local/billing/cancel"
    # ---- Mock billing (development/tests) ----
    MOCK_BILLING_SECRET: str = "orb-mock-billing-secret"

    # ---- Module 9: AI Trading Intelligence -----------------------------
    # All AI settings are OPTIONAL. If EMERGENT_LLM_KEY is unset or
    # AI_ENABLED is False, the AIService silently falls back to the
    # deterministic rule-based provider — no startup failure, every
    # endpoint keeps working.
    AI_ENABLED: bool = True
    # Provider selector. "gpt52" (v1.0.0 alias, OpenAI family), "openai",
    # "anthropic", "gemini" — all route through the Emergent Universal
    # LLM key. "rule_based" bypasses external LLMs entirely.
    AI_PROVIDER: Literal[
        "gpt52", "openai", "anthropic", "gemini", "rule_based"
    ] = "gpt52"
    AI_MODEL: str = "gpt-5.2"
    AI_TEMPERATURE: float | None = None
    AI_MAX_TOKENS: int | None = None
    EMERGENT_LLM_KEY: str | None = None
    AI_CACHE_TTL_S: int = 600
    AI_REQUEST_TIMEOUT_S: float = 25.0

    # ---- Module 10 (Enterprise & Production) ---------------------------
    # Security
    SECURITY_HSTS_ENABLED: bool = True
    SECURITY_HSTS_MAX_AGE: int = 31_536_000
    SECURITY_CSP_ENABLED: bool = True
    SECURITY_CSP: str | None = None

    # Rate limiting (token bucket)
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 120
    RATE_LIMIT_BURST: int = 240
    RATE_LIMIT_EXEMPT_PATHS: List[str] = Field(
        default_factory=lambda: [
            "/health",
            "/api/v1/health",
            "/api/admin-ui",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/v1/monitoring/metrics/prometheus",
        ]
    )

    # ---- Milestone 8 (Execution Safety) — env defaults ----------------
    # These are only used when the DB `execution_safety_settings` row is
    # absent. Once an admin edits the settings via the panel, the DB row
    # wins.
    EXEC_TRADES_PER_SECOND: int = 8
    EXEC_ORDERS_PER_MINUTE: int = 100
    EXEC_ORDERS_PER_HOUR: int = 2000
    EXEC_GLOBAL_ORDERS_PER_SECOND: int = 200
    EXEC_GLOBAL_ORDERS_PER_MINUTE: int = 5000
    EXEC_DUPLICATE_WINDOW_SECONDS: float = 1.0
    EXEC_DUPLICATE_ACTION: str = "reject"          # "reject" | "queue"
    EXEC_QUEUE_ENABLED: bool = True
    EXEC_QUEUE_MAX_SIZE: int = 500
    EXEC_QUEUE_TIMEOUT_SECONDS: float = 30.0
    EXEC_AUTO_PAUSE_ENABLED: bool = True
    EXEC_AUTO_PAUSE_VIOLATIONS: int = 10
    EXEC_AUTO_PAUSE_WINDOW_SECONDS: int = 60
    EXEC_EVENT_RETENTION_DAYS: int = 90

    # ---- Milestone 9 (Risk Management) — env defaults -----------------
    RISK_BREACH_RETENTION_DAYS: int = 90
    # Cron schedule for the retention prune job (execution_safety_events
    # + risk_breaches). Runs daily at 04:00 in SCHEDULER_TIMEZONE.
    RISK_RETENTION_CRON: str = "0 4 * * *"

    # Performance
    SLOW_REQUEST_THRESHOLD_MS: int = 1500

    # Logging (rotation)
    LOG_FILE_ENABLED: bool = False
    LOG_FILE_PATH: str = "/var/log/orb_ai/app.log"
    LOG_FILE_MAX_BYTES: int = 20 * 1024 * 1024
    LOG_FILE_BACKUP_COUNT: int = 10

    # Backup & Recovery
    BACKUP_ENABLED: bool = False
    BACKUP_LOCAL_DIR: str = "/var/backups/orb_ai"
    BACKUP_RETENTION_DAYS: int = 14
    BACKUP_SCHEDULE_CRON: str = "0 3 * * *"

    # S3-compatible
    BACKUP_S3_ENABLED: bool = False
    BACKUP_S3_ENDPOINT: str | None = None
    BACKUP_S3_BUCKET: str | None = None
    BACKUP_S3_REGION: str | None = None
    BACKUP_S3_ACCESS_KEY: str | None = None
    BACKUP_S3_SECRET_KEY: str | None = None
    BACKUP_S3_PREFIX: str = "postgres"
    BACKUP_ENCRYPTION_KEY: str | None = None

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if v is None or v == "":
            return ["*"]
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @model_validator(mode="after")
    def _enforce_production_secrets(self) -> "Settings":
        """Fail fast in production when security-critical secrets are unset
        or left at their insecure development defaults.

        This guard only trips when ``APP_ENV=production``; development,
        staging and test boots are unaffected. It prevents a deployment
        from silently coming up with a forgeable JWT secret or without an
        encryption key for at-rest broker credentials.
        """
        if self.APP_ENV != "production":
            return self

        problems: list[str] = []

        def missing_or_placeholder(value: str | None) -> bool:
            return not value or "replace_me" in value.lower()

        if self.DEBUG:
            problems.append("DEBUG must be false in production")
        if (
            missing_or_placeholder(self.JWT_SECRET_KEY)
            or self.JWT_SECRET_KEY == "dev-insecure-secret-change-me"
        ):
            problems.append("JWT_SECRET_KEY must be set to a strong unique value")
        if missing_or_placeholder(self.ENCRYPTION_KEY):
            problems.append(
                "ENCRYPTION_KEY (Fernet) must be set for at-rest broker "
                "credential encryption"
            )
        else:
            try:
                from cryptography.fernet import Fernet

                Fernet(self.ENCRYPTION_KEY.encode())
            except (TypeError, ValueError):
                problems.append("ENCRYPTION_KEY must be a valid Fernet key")
        # Reject wildcard / empty CORS in production. A "*" origin combined
        # with allow_credentials=True is both a security risk and rejected
        # by browsers, so fail fast instead of booting a broken config.
        if not self.CORS_ORIGINS or "*" in self.CORS_ORIGINS:
            problems.append(
                "CORS_ORIGINS must list explicit production origins "
                "(e.g. https://orb-ai.co.in); '*' is not allowed in production"
            )
        # Reject the insecure development database default in production.
        if (
            missing_or_placeholder(self.DATABASE_URL)
            or self.DATABASE_URL
            == "postgresql+asyncpg://orb:orb@localhost:5432/orb_ai"
        ):
            problems.append(
                "DATABASE_URL must point at the production database "
                "(the dev default with 'orb:orb@localhost' is not allowed)"
            )
        if self.REDIS_ENABLED and missing_or_placeholder(self.REDIS_URL):
            problems.append("REDIS_URL must not contain an unset placeholder")
        # ---- Billing must fail closed in production -------------------
        # Never let production silently run on mock billing, and require the
        # real credentials (incl. webhook secret) for the selected provider so
        # a payment cannot be "confirmed" without verifiable provider events.
        billing = (self.BILLING_PROVIDER or "noop").lower()
        if billing == "mock":
            problems.append(
                "BILLING_PROVIDER=mock is not allowed in production "
                "(mock billing must never confirm real payments)"
            )
        elif billing == "razorpay":
            if missing_or_placeholder(self.RAZORPAY_KEY_ID) or missing_or_placeholder(self.RAZORPAY_KEY_SECRET):
                problems.append(
                    "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are required "
                    "when BILLING_PROVIDER=razorpay in production"
                )
            if missing_or_placeholder(self.RAZORPAY_WEBHOOK_SECRET):
                problems.append(
                    "RAZORPAY_WEBHOOK_SECRET is required in production so "
                    "webhook signatures are always verified"
                )
        elif billing == "stripe":
            if missing_or_placeholder(self.STRIPE_SECRET_KEY):
                problems.append(
                    "STRIPE_SECRET_KEY is required when "
                    "BILLING_PROVIDER=stripe in production"
                )
            if missing_or_placeholder(self.STRIPE_WEBHOOK_SECRET):
                problems.append(
                    "STRIPE_WEBHOOK_SECRET is required in production so "
                    "webhook signatures are always verified"
                )
        if problems:
            raise ValueError(
                "Insecure production configuration: " + "; ".join(problems)
            )
        return self


    # ---- Upstox read-only market-data credential resolution ----------
    # Single source of truth for "which token authenticates the READ-ONLY
    # Upstox market-data provider". Priority: real access token first, then
    # the analytics (read-only) token. Order-execution paths must NOT use
    # these — see app.engine.market_data.upstox_credentials.
    @property
    def upstox_market_data_token(self) -> str | None:
        """Effective READ-ONLY Upstox market-data token (never for orders).

        Returns the trimmed ``UPSTOX_ACCESS_TOKEN`` when present, else the
        trimmed ``UPSTOX_ANALYTICS_TOKEN``, else ``None``.
        """
        access = (self.UPSTOX_ACCESS_TOKEN or "").strip()
        if access:
            return access
        analytics = (self.UPSTOX_ANALYTICS_TOKEN or "").strip()
        if analytics:
            return analytics
        return None

    @property
    def upstox_market_data_token_source(self) -> str | None:
        """Which env var backs :pyattr:`upstox_market_data_token`.

        ``"access_token"`` | ``"analytics_token"`` | ``None``. Safe to log
        (it never contains the token itself).
        """
        if (self.UPSTOX_ACCESS_TOKEN or "").strip():
            return "access_token"
        if (self.UPSTOX_ANALYTICS_TOKEN or "").strip():
            return "analytics_token"
        return None

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_test(self) -> bool:
        return self.APP_ENV == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
