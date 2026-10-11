from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

BASE_DIR = Path(__file__).resolve().parents[3]


class DbSettings(BaseModel):
    """同步 PostgreSQL 连接配置"""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1)
    port: int = Field(default=5432, ge=1, le=65535)
    username: str = Field(min_length=1)
    hashed_password: SecretStr = Field(min_length=1)
    name: str = Field(min_length=1)

    driver: Literal["postgresql+psycopg2"] = "postgresql+psycopg2"

    @property
    def url(self) -> URL:
        return URL.create(
            drivername=self.driver,
            username=self.username,
            password=self.hashed_password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.name,
        )


class MigrationSettings(BaseSettings):
    """只在迁移命令中实例化"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env.migration",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="forbid",
        case_sensitive=False,
    )

    migration_db: DbSettings

    @model_validator(mode="after")
    def validate_role(self) -> Self:
        if self.migration_db.username != "migration_owner":
            raise ValueError("迁移链接必须使用 migration_owner")
        return self


class AdminSettings(BaseSettings):
    """只供初始化和测试数据库管理使用"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env.admin",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="forbid",
        case_sensitive=False,
    )

    admin_db: DbSettings
