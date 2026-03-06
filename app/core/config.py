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
    jwt_expire_minutes: int = Field(default=60, alias="JWT_EXPIRE_MINUTES")

    # Uploads
    upload_dir: str = Field(default="uploads", alias="UPLOAD_DIR")
    max_file_size_mb: int = Field(default=10, alias="MAX_FILE_SIZE_MB")

    # Email (we saw these in your .env)
    email_provider: str = Field(default="mock", alias="EMAIL_PROVIDER")  # mock | smtp | providerX
    smtp_host: str = Field(default="localhost", alias="SMTP_HOST")
    smtp_port: int = Field(default=1025, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_pass: str = Field(default="", alias="SMTP_PASS")

    # Storage (we saw these in your .env)
    storage_provider: str = Field(default="local", alias="STORAGE_PROVIDER")  # local | s3
    s3_endpoint_url: str = Field(default="", alias="S3_ENDPOINT_URL")
    s3_access_key: str = Field(default="", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="", alias="S3_SECRET_KEY")
    s3_bucket: str = Field(default="voicechat", alias="S3_BUCKET")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")


settings = Settings()