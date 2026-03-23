import json
import os
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = os.getenv("ENV_FILE", ".env")

class Settings(BaseSettings):
    # Keep strict (forbidden extras) so you catch typos in env vars early
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="dev", alias="APP_ENV")
    cors_allowed_origins: list[str] = Field(
        default=["*"],
        alias="CORS_ALLOWED_ORIGINS"
    )

    web_app_name: str = Field(default="Voice Chat", alias="WEB_APP_NAME")
    web_app_url: str = Field(default="http://localhost:3000", alias="WEB_APP_URL")

    # Discovery
    discovery_code_ttl_seconds: int = Field(default=60 * 60, alias="DISCOVERY_CODE_TTL_SECONDS")
    discovery_link_ttl_seconds: int = Field(default=60 * 60 * 24, alias="DISCOVERY_LINK_TTL_SECONDS")

    # Mongo
    mongo_uri: str = Field(default="mongodb://mongo:27017", alias="MONGO_URI")
    mongo_db: str = Field(default="voice_chat", alias="MONGO_DB")

    # JWT
    jwt_secret: str = Field(default="supersecret", alias="JWT_SECRET")
    jwt_alg: str = Field(default="HS256", alias="JWT_ALG")
    access_token_expire_minutes: int = Field(default=60, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, alias="REFRESH_TOKEN_EXPIRE_DAYS")

    # Passkey
    passkey_rp_id: str = Field(default="localhost", alias="PASSKEY_RP_ID")
    passkey_rp_name: str = Field(default="Voice Chat", alias="PASSKEY_RP_NAME")
    passkey_origin: str = Field(default="http://localhost:5173", alias="PASSKEY_ORIGIN")
    passkey_challenge_ttl_seconds: int = Field(
        default=300,
        alias="PASSKEY_CHALLENGE_TTL_SECONDS",
    )

    # Uploads
    upload_dir: str = Field(default="uploads", alias="UPLOAD_DIR")
    max_file_size_mb: int = Field(default=10, alias="MAX_FILE_SIZE_MB")

    # Email (we saw these in your .env)
    email_provider: str = Field(default="mock", alias="EMAIL_PROVIDER")  # mock | smtp | providerX
    smtp_host: str = Field(default="localhost", alias="SMTP_HOST")
    smtp_port: int = Field(default=1025, alias="SMTP_PORT")
    smtp_from_email: str = Field(default="no-reply@voicechat.local", alias="SMTP_FROM_EMAIL")
    smtp_from_name: str = Field(default="Voice Chat", alias="SMTP_FROM_NAME")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_pass: str = Field(default="", alias="SMTP_PASS")

    # Rate Limit
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_storage_uri: str = Field(default="async+memory://", alias="RATE_LIMIT_STORAGE_URI")

    # Storage (we saw these in your .env)
    storage_provider: str = Field(default="local", alias="STORAGE_PROVIDER")  # local | s3
    s3_endpoint_url: str = Field(default="", alias="S3_ENDPOINT_URL")
    s3_access_key: str = Field(default="", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="", alias="S3_SECRET_KEY")
    s3_bucket: str = Field(default="voicechat", alias="S3_BUCKET")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")

    # RabbitMQ
    rabbitmq_url: str = Field(
        default="amqp://guest:guest@rabbitmq:5672/",
        alias="RABBITMQ_URL",
    )
    email_queue_name: str = Field(
        default="email.send",
        alias="EMAIL_QUEUE_NAME",
    )

    # Redis
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    presence_backend: str = Field(default="memory", alias="PRESENCE_BACKEND")
    presence_key_prefix: str = Field(default="presence", alias="PRESENCE_KEY_PREFIX")

    socketio_queue_backend: str = Field(default="memory", alias="SOCKETIO_QUEUE_BACKEND")
    socketio_redis_url: str = Field(default="redis://redis:6379/0", alias="SOCKETIO_REDIS_URL")

    # Calls / WebRTC
    call_ring_timeout_seconds: int = Field(default=45, alias="CALL_RING_TIMEOUT_SECONDS")
    call_reconnect_grace_seconds: int = Field(default=15, alias="CALL_RECONNECT_GRACE_SECONDS")
    call_session_backend: str = Field(default="memory", alias="CALL_SESSION_BACKEND")
    call_session_key_prefix: str = Field(default="call_sessions", alias="CALL_SESSION_KEY_PREFIX")
    call_stun_urls: list[str] = Field(
        default=["stun:stun.l.google.com:19302"],
        alias="CALL_STUN_URLS",
    )
    call_turn_urls: list[str] = Field(
        default=["turn:localhost:3478?transport=udp", "turn:localhost:3478?transport=tcp"],
        alias="CALL_TURN_URLS",
    )
    turn_realm: str = Field(default="voicechat", alias="TURN_REALM")
    turn_auth_secret: str = Field(default="voicechat-turn-secret", alias="TURN_AUTH_SECRET")
    turn_credential_ttl_seconds: int = Field(default=3600, alias="TURN_CREDENTIAL_TTL_SECONDS")

    turn_provider: str = Field(default="coturn", alias="TURN_PROVIDER")
    turn_multi: bool = Field(default=False, alias="TURN_MULTI")
    # Coturn settings
    coturn_urls: list[str] = Field(
        default=["turn:127.0.0.1:3478"],
        alias="COTURN_URLS",
    )
    coturn_username: str = Field(default="", alias="COTURN_USERNAME")
    coturn_password: str = Field(default="", alias="COTURN_PASSWORD")
    turn_ttl_seconds: int = Field(default=3600, alias="TURN_TTL")
    # Cloudflare settings
    cf_turn_key_id: str = Field(default="", alias="CF_TURN_KEY_ID")
    cf_turn_api_token: str = Field(default="", alias="CF_TURN_API_TOKEN")
    
    cf_account_id: str = Field(default="", alias="CF_ACCOUNT_ID")
    cf_account_token: str = Field(default="", alias="CF_ACCOUNT_TOKEN")
    cf_turn_pause_at_gb: float = Field(default=999.0, alias="CF_TURN_PAUSE_AT_GB")
    cf_turn_usage_lookback_days: int = Field(default=30, alias="CF_TURN_USAGE_LOOKBACK_DAYS")
    cf_turn_usage_cache_seconds: int = Field(default=300, alias="CF_TURN_USAGE_CACHE_SECONDS")

    @field_validator("cors_allowed_origins", "call_stun_urls", "coturn_urls", "call_turn_urls", mode="before")
    @classmethod
    def parse_list_settings(cls, value: Any) -> list[str] | Any:
        if isinstance(value, list):
            return value

        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return []

            if normalized.startswith("["):
                parsed = json.loads(normalized)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]

            return [item.strip() for item in normalized.split(",") if item.strip()]

        return value


settings = Settings()
