import uuid
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.models.projects import Project


class CreateProjectRequest(BaseModel):
    """创建项目请求：组织、创建者和初始状态由服务端决定。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="项目名称，去掉两端空白后为 1 到 200 个字符",
        examples=["演示市政道路工程"],
    )
    construction_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="工程类型，去掉两端空白后为 1 到 100 个字符",
        examples=["市政道路"],
    )
    description: str = Field(
        default="",
        max_length=2000,
        description="项目描述，默认空字符串，最多 2000 个字符，保留空白和换行",
    )
    data_classification: Literal["synthetic", "deidentified", "confidential"] = Field(
        default="synthetic",
        description="数据分类：synthetic 模拟数据、deidentified 脱敏数据、confidential 保密数据",
    )

    @field_validator(
        "name",
        "construction_type",
        mode="before",
    )
    @classmethod
    def strip_name_fields(cls, value: object) -> object:
        """先去掉名称两端的空白，再让 Field 检查长度。"""
        if isinstance(value, str):
            return value.strip()
        return value


class UpdateProjectRequest(BaseModel):
    """修改项目请求：版本号必填，只修改客户端实际提交的字段。"""

    model_config = ConfigDict(extra="forbid")

    expected_lock_version: int = Field(
        ...,
        ge=1,
        strict=True,
        description="用户读取项目时得到的版本号，必须是大于等于 1 的整数，用于乐观锁校验",
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="新项目名称；省略则保持原值，提供时去掉两端空白，不允许 null",
    )
    construction_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="新工程类型；省略则保持原值，提供时去掉两端空白，不允许 null",
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="新项目描述；省略保持原值，空字符串表示清空，不允许 null",
    )
    data_classification: Literal["synthetic", "deidentified", "confidential"] | None = Field(
        default=None,
        description="新数据分类，仅支持 synthetic、deidentified、confidential；省略保持原值，不允许 null",
    )

    @field_validator(
        "name",
        "construction_type",
        mode="before",
    )
    @classmethod
    def strip_name_fields(cls, value: object) -> object:
        """只处理字符串，不在这里替其他字段做类型转换。"""
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def check_changes(self) -> Self:
        """拒绝空修改和显式 null；没有提交的字段保持原值。"""
        changed_fields = self.model_fields_set - {"expected_lock_version"}

        if not changed_fields:
            raise ValueError("至少提供一个可修改字段")

        for field_name in changed_fields:
            if getattr(self, field_name) is None:
                raise ValueError("已提供的修改字段不能为 null")

        return self


class ArchiveProjectRequest(BaseModel):
    """归档项目请求：只接收用户看到的版本号。"""

    model_config = ConfigDict(extra="forbid")

    expected_lock_version: int = Field(
        ...,
        ge=1,
        strict=True,
        description="用户读取项目时得到的版本号，必须是大于等于 1 的整数，归档时须与当前版本一致",
    )


class ProjectResponse(BaseModel):
    """项目响应字段，与 API 文档第 3.1 节保持一致。"""

    id: uuid.UUID = Field(
        ...,
        description="项目 UUID，由服务端生成",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="项目名称，1 到 200 个字符",
    )
    construction_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="工程类型，1 到 100 个字符",
    )
    description: str = Field(
        ...,
        max_length=2000,
        description="项目描述，最多 2000 个字符，没有描述时为空字符串",
    )
    data_classification: Literal["synthetic", "deidentified", "confidential"] = Field(
        ...,
        description="数据分类：synthetic 模拟数据、deidentified 脱敏数据、confidential 保密数据",
    )
    status: Literal["active", "archived"] = Field(
        ...,
        description="项目状态：active 可正常使用，archived 已归档且只读",
    )
    current_baseline_id: uuid.UUID | None = Field(
        ...,
        description="当前项目基准的 UUID；尚未发布基准时为 null，字段仍需返回",
    )
    baseline_dirty: bool = Field(
        ...,
        strict=True,
        description="是否存在尚未重新发布到基准的变更；创建项目时为 true",
    )
    lock_version: int = Field(
        ...,
        ge=1,
        strict=True,
        description="当前项目锁版本，大于等于 1；每次成功修改或归档后递增",
    )
    created_by: uuid.UUID = Field(
        ...,
        description="项目创建者的用户 UUID，由服务端根据当前认证身份填写",
    )
    created_at: datetime = Field(
        ...,
        description="项目创建时间，服务端按 UTC ISO 8601 格式返回",
    )
    updated_at: datetime = Field(
        ...,
        description="项目最近修改时间，服务端按 UTC ISO 8601 格式返回",
    )


def utc_text(value: datetime) -> str:
    """将数据库时间统一转换成带 Z 的 UTC 时间字符串。"""
    utc_value = value.astimezone(UTC)
    return utc_value.isoformat().replace("+00:00", "Z")


def project_summary(project: Project) -> dict[str, object]:
    """项目摘要：只返回接口允许公开的字段。"""
    current_baseline_id = None
    if project.current_baseline_id is not None:
        current_baseline_id = str(project.current_baseline_id)

    return {
        "id": str(project.id),
        "name": project.name,
        "construction_type": project.construction_type,
        "description": project.description,
        "data_classification": project.data_classification,
        "status": project.status,
        "current_baseline_id": current_baseline_id,
        "baseline_dirty": project.baseline_dirty,
        "lock_version": project.lock_version,
        "created_by": str(project.created_by),
        "created_at": utc_text(project.created_at),
        "updated_at": utc_text(project.updated_at),
    }
