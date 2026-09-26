import os
from dataclasses import dataclass


def csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(x.strip() for x in os.getenv(name, default).split(",") if x.strip())


def boolean(name: str, default: bool = False) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "postgresql://music_app:music_app@localhost:54329/music")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:63799/0")
    provider_base_url: str = os.getenv("PROVIDER_BASE_URL", "http://localhost:8010").rstrip("/")
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    credits_per_candidate: int = int(os.getenv("GENERATION_CREDIT_PER_CANDIDATE", "10"))
    music_provider: str = os.getenv("MUSIC_PROVIDER", "emulator")
    provider_approval_status: str = os.getenv("PROVIDER_APPROVAL_STATUS", "development_only")
    provider_contract_version: str = os.getenv("PROVIDER_CONTRACT_VERSION", "local-emulator-v1")
    provider_adapter_version: str = os.getenv("PROVIDER_ADAPTER_VERSION", "3.0.0")
    jwt_secret: str = os.getenv("JWT_SECRET", "local-development-jwt-secret-change-before-production-0123456789abcdef")
    jwt_ttl_minutes: int = int(os.getenv("JWT_TTL_MINUTES", "30"))
    refresh_ttl_days: int = int(os.getenv("REFRESH_TTL_DAYS", "14"))
    cookie_secure: bool = boolean("COOKIE_SECURE", False)
    cookie_domain: str | None = os.getenv("COOKIE_DOMAIN") or None
    media_signing_secret: str = os.getenv("MEDIA_SIGNING_SECRET", "local-development-media-secret-change-before-production-0123456789")
    provider_webhook_secret: str = os.getenv("PROVIDER_WEBHOOK_SECRET", "dev-provider-webhook-secret")
    cors_origins: tuple[str, ...] = csv("CORS_ORIGINS", "http://localhost:4173,http://localhost:4174,http://localhost:8080")
    s3_endpoint_url: str = os.getenv("S3_ENDPOINT_URL", "http://localhost:9000")
    s3_access_key: str = os.getenv("S3_ACCESS_KEY", "minioadmin")
    s3_secret_key: str = os.getenv("S3_SECRET_KEY", "minioadmin")
    s3_bucket: str = os.getenv("S3_BUCKET", "music-assets")
    media_token_ttl_seconds: int = int(os.getenv("MEDIA_TOKEN_TTL_SECONDS", "300"))
    payment_provider: str = os.getenv("PAYMENT_PROVIDER", "emulator")
    payment_base_url: str = os.getenv("PAYMENT_BASE_URL", "http://payment-emulator:8020").rstrip("/")
    payment_webhook_secret: str = os.getenv("PAYMENT_WEBHOOK_SECRET", "dev-payment-webhook-secret")
    public_api_base_url: str = os.getenv("PUBLIC_API_BASE_URL", "http://api:8000").rstrip("/")
    request_rate_limit_per_minute: int = int(os.getenv("REQUEST_RATE_LIMIT_PER_MINUTE", "300"))
    login_rate_limit_per_minute: int = int(os.getenv("LOGIN_RATE_LIMIT_PER_MINUTE", "10"))
    moderation_policy_version: str = os.getenv("MODERATION_POLICY_VERSION", "policy-v1")
    db_pool_min: int = max(1, int(os.getenv("DB_POOL_MIN", "2")))
    db_pool_max: int = max(int(os.getenv("DB_POOL_MIN", "2")), int(os.getenv("DB_POOL_MAX", "16")))
    db_connect_timeout_seconds: int = max(1, int(os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "5")))
    db_pool_acquire_timeout_seconds: float = max(0.1, float(os.getenv("DB_POOL_ACQUIRE_TIMEOUT_SECONDS", "5")))
    db_statement_timeout_ms: int = max(1000, int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "15000")))
    media_delivery_mode: str = os.getenv("MEDIA_DELIVERY_MODE", "proxy").strip().lower()
    # No default on purpose: an unset key disables the second-factor endpoints with a clear
    # error instead of sealing secrets under a value anyone could guess.
    mfa_encryption_key: str = os.getenv("MFA_ENCRYPTION_KEY", "")
    mfa_enrolment_window_seconds: int = max(60, int(os.getenv("MFA_ENROLMENT_WINDOW_SECONDS", "900")))
    mfa_pending_ttl_seconds: int = max(60, int(os.getenv("MFA_PENDING_TTL_SECONDS", "300")))
    mfa_challenge_rate_limit_per_minute: int = int(os.getenv("MFA_CHALLENGE_RATE_LIMIT_PER_MINUTE", "6"))
    s3_public_endpoint_url: str = os.getenv("S3_PUBLIC_ENDPOINT_URL", "").rstrip("/")


settings = Settings()
