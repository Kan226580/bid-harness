import pytest
from pydantic import ValidationError

from backend.app.api.schemas import CursorPage, SuccessResponse
from backend.app.core.errors import ApiError
from backend.app.core.pagination import CursorPayload
from backend.app.modules.operations.idempotency import validate_idempotency_key
from backend.app.modules.projects.schemas import (
    ArchiveProjectRequest,
    CreateProjectRequest,
    ProjectResponse,
    UpdateProjectRequest,
)


def test_create_defaults_and_server_fields() -> None:
    """验证创建默认值，并拒绝客户端传入服务端字段。"""

    payload = CreateProjectRequest(name=" 测试工程 ", construction_type="市政道路")
    assert payload.name == "测试工程"
    assert payload.description == ""
    assert payload.data_classification == "synthetic"
    with pytest.raises(ValidationError):
        CreateProjectRequest.model_validate(
            {
                "name": "测试",
                "construction_type": "道路",
                "org_id": "forged",
            }
        )
    for name in ["", "   ", "长" * 201]:
        with pytest.raises(ValidationError):
            CreateProjectRequest(name=name, construction_type="道路")


@pytest.mark.parametrize(
    "payload",
    [
        {"expected_lock_version": 1},
        {"expected_lock_version": 0, "description": "x"},
        {"expected_lock_version": True, "description": "x"},
        {"expected_lock_version": "1", "description": "x"},
        {"expected_lock_version": 1, "description": None},
        {"expected_lock_version": 1, "status": "archived"},
    ],
)
def test_invalid_patch(payload: dict[str, object]) -> None:
    """空修改、错误版本类型、null 和未知字段都应被拒绝。"""

    with pytest.raises(ValidationError):
        UpdateProjectRequest.model_validate(payload)


def test_patch_can_clear_description() -> None:
    """空字符串可以清空描述，省略字段与清空字段不同。"""

    payload = UpdateProjectRequest(expected_lock_version=1, description="")
    assert payload.model_dump(exclude_unset=True) == {
        "expected_lock_version": 1,
        "description": "",
    }


def test_idempotency_key_validation() -> None:
    """验证幂等键可选、必需以及长度和字符边界。"""

    assert validate_idempotency_key(None, required=False) is None
    assert validate_idempotency_key("valid-key-123", required=True) == "valid-key-123"
    for key in [None, "short", "中文不能作为key", "x" * 129, "invalid key"]:
        with pytest.raises(ApiError):
            validate_idempotency_key(key, required=True)


def test_all_pydantic_fields_have_descriptions() -> None:
    """请求、响应和内部游标模型的每个字段都应有明确说明。"""
    models = (
        SuccessResponse[ProjectResponse],
        CursorPage[ProjectResponse],
        CursorPayload,
        CreateProjectRequest,
        UpdateProjectRequest,
        ArchiveProjectRequest,
        ProjectResponse,
    )

    for model in models:
        for field_name, field_info in model.model_fields.items():
            assert field_info.description, f"{model.__name__}.{field_name} 缺少字段说明"


def test_common_response_field_constraints() -> None:
    """验证请求编号、分页数量和可空必填字段的约束。"""
    with pytest.raises(ValidationError):
        SuccessResponse[dict[str, object]].model_validate(
            {
                "data": {},
                "request_id": "invalid-id",
            }
        )

    with pytest.raises(ValidationError):
        CursorPage[object].model_validate(
            {
                "items": [{} for _ in range(101)],
                "next_cursor": None,
                "has_more": False,
            }
        )

    with pytest.raises(ValidationError):
        CursorPage[object].model_validate(
            {
                "items": [],
                "has_more": False,
            }
        )

    page = CursorPage[object].model_validate(
        {
            "items": [],
            "next_cursor": None,
            "has_more": False,
        }
    )
    assert page.next_cursor is None
