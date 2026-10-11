from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.app.core.db_setting import BASE_DIR, DbSettings

# 环境名称
EnvName = Literal["dev", "test", "prod"]


class RedisSettings(BaseModel):
    """Redis settings"""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(..., min_length=1, description="redis主机")
    port: int = Field(default=6379, ge=1, le=65535, description="redis端口")
    db: int = Field(default=0, ge=0, description="redis数据库")
    hashed_password: SecretStr = Field(..., description="redis密码")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        pwd = self.hashed_password.get_secret_value()
        return f"redis://:{pwd}@{self.host}:{self.port}/{self.db}"


class SecuritySettings(BaseModel):
    """Security settings"""

    model_config = ConfigDict(extra="forbid")

    csrf_signing_key: SecretStr = Field(..., min_length=32, description="CSRF 签名密钥")
    session_ttl_hours: int = Field(default=12, ge=1, le=72, description="会话有效期（小时）")
    public_origin: str = Field(default="http://127.0.0.1:8000", description="允许的同源地址")


class Settings(BaseSettings):
    """应用配置根模型"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",  # REDIS__PORT -> redis.port
        extra="forbid",
        case_sensitive=False,
    )

    env: EnvName = "prod"

    db: DbSettings
    auth_db: DbSettings
    ops_db: DbSettings

    redis: RedisSettings
    security: SecuritySettings

    @model_validator(mode="after")
    def validate_database_connections(self) -> Self:
        expected_roles = (
            (self.db, "app_runtime"),
            (self.auth_db, "auth_service"),
            (self.ops_db, "ops_runtime"),
        )

        for config, expected_role in expected_roles:
            if config.username != expected_role:
                raise ValueError(f"数据库连接必须使用 {expected_role}")

        target = (self.db.host, self.db.port, self.db.name)

        for config in (self.auth_db, self.ops_db):
            if (config.host, config.port, config.name) != target:
                raise ValueError("业务、认证和调度连接必须指向同一数据库")

        return self


settings = Settings()
