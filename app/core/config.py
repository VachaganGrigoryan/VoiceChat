import os

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

_ENV_FILE = os.getenv("ENV_FILE", ".env")

class Settings(BaseSettings):
    # Keep strict (forbidden extras) so you catch typos in env vars early
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="dev", alias="APP_ENV")

    # Mongo
    mongo_uri: str = Field(alias="MONGO_URI")
    mongo_db: str = Field(default="voice_chat", alias="MONGO_DB")

    # JWT
    jwt_secret: str = Field(default="supersecret", alias="JWT_SECRET")
    jwt_alg: str = Field(default="HS256", alias="JWT_ALG")
    access_token_expire_minutes: int = Field(default=60, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, alias="REFRESH_TOKEN_EXPIRE_DAYS")

    # Uploads
    upload_dir: str = Field(default="uploads", alias="UPLOAD_DIR")
    max_file_size_mb: int = Field(default=10, alias="MAX_FILE_SIZE_MB")

    # Email (we saw these in your .env)
    email_provider: str = Field(default="mock", alias="EMAIL_PROVIDER")  # mock | smtp | providerX
    smtp_host: str = Field(default="localhost", alias="SMTP_HOST")
    smtp_port: int = Field(default=1025, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_pass: str = Field(default="", alias="SMTP_PASS")

    # Rate Limit
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


settings = Settings()