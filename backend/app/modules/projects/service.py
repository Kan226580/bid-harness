import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.authorization import Permission, authorize
from backend.app.core.errors import ApiError
from backend.app.core.pagination import CursorAnchor, paginate
from backend.app.core.principal import Principal
from backend.app.db.tenant import tenant_transaction
from backend.app.models.chapters import Chapter
from backend.app.models.projects import Project
from backend.app.modules.operations.idempotency import WriteResult, execute_idempotent
from backend.app.modules.projects.repository import (
    fetch_projects,
    find_project,
    mark_chapters_for_review,
    update_project_if_version_matches,
)
from backend.app.modules.projects.schemas import project_summary

DEFAULT_CHAPTERS = (
    ("施工方案", "construction"),
    ("进度安排", "schedule"),
    ("质量保证", "quality"),
    ("安全文明施工", "safety"),
    ("资源配置", "resources"),
)


# ----- 公共检查


def resource_not_found() -> ApiError:
    """不存在和跨组织资源都使用同一种错误。"""
    return ApiError(
        404,
        "RESOURCE_NOT_FOUND",
        "资源不存在",
    )


def require_project(
    db: Session,
    actor: Principal,
    *,
    project_id: uuid.UUID,
) -> Project:
    """查询本组织项目，查不到就停止当前操作。"""
    project = find_project(
        db,
        org_id=actor.org_id,
        project_id=project_id,
    )

    if project is None:
        raise resource_not_found()

    return project


def require_active(project: Project) -> None:
    """归档项目只允许读取。"""
    if project.status == "archived":
        raise ApiError(
            409,
            "PROJECT_ARCHIVED",
            "归档项目只读",
        )


def project_update_error(
    db: Session,
    actor: Principal,
    *,
    project_id: uuid.UUID,
) -> ApiError:
    """更新零行后重新查询，只用于区分不存在、归档和版本冲突。"""
    current = require_project(
        db,
        actor,
        project_id=project_id,
    )

    if current.status == "archived":
        return ApiError(
            409,
            "PROJECT_ARCHIVED",
            "归档项目只读",
        )

    return ApiError(
        409,
        "VERSION_CONFLICT",
        "内容已更新，请比较最新版本后重新保存。",
        details={"current_lock_version": current.lock_version},
    )


# ----- 查询


def get_project(
    actor: Principal,
    session_factory: sessionmaker[Session],
    *,
    project_id: uuid.UUID,
) -> dict[str, object]:
    """读取项目详情，保留 A5 的身份和租户事务入口。"""
    with tenant_transaction(actor, session_factory) as db:
        project = require_project(
            db,
            actor,
            project_id=project_id,
        )

        authorize(actor, Permission.READ_CONTENT)

        return project_summary(project)


def list_projects(
    actor: Principal,
    session_factory: sessionmaker[Session],
    *,
    q: str | None,
    status: str | None,
    limit: int,
    cursor: str | None,
) -> dict[str, object]:
    """按过滤条件和稳定游标查询本组织项目。"""
    authorize(actor, Permission.READ_CONTENT)

    filters: dict[str, object] = {
        "resource": "projects",
        "org_id": str(actor.org_id),
        "user_id": str(actor.user_id),
        "q": q,
        "status": status,
    }

    with tenant_transaction(actor, session_factory) as db:

        def fetch_page(anchor: CursorAnchor | None, size: int) -> Sequence[Project]:
            """公共分页工具回调这里，用当前事务查询一页项目。"""
            return fetch_projects(
                db,
                org_id=actor.org_id,
                q=q,
                status=status,
                anchor=anchor,
                size=size,
            )

        def project_anchor(project: Project) -> CursorAnchor:
            """从项目记录中取出下一页需要的两个排序字段。"""
            return CursorAnchor(
                created_at=project.created_at,
                id=project.id,
            )

        items, next_cursor, has_more = paginate(
            limit=limit,
            cursor=cursor,
            filters=filters,
            fetch=fetch_page,
            anchor_of=project_anchor,
        )

        return {
            "items": [project_summary(project) for project in items],
            "next_cursor": next_cursor,
            "has_more": has_more,
        }


# ----- 创建


def create_project(
    actor: Principal,
    session_factory: sessionmaker[Session],
    *,
    name: str,
    construction_type: str,
    description: str = "",
    data_classification: str = "synthetic",
    request_id: str,
    idempotency_key: str | None,
) -> WriteResult:
    """在同一事务创建项目、默认五章和可选的幂等结果。"""
    authorize(actor, Permission.EDIT_CONTENT)

    request_data = {
        "name": name,
        "construction_type": construction_type,
        "description": description,
        "data_classification": data_classification,
    }

    with tenant_transaction(actor, session_factory) as db:

        def create_project_action() -> WriteResult:
            """由幂等执行器调用，始终使用外层已经开启的 db。"""
            project = Project(
                id=uuid.uuid4(),
                org_id=actor.org_id,
                created_by=actor.user_id,
                name=name,
                construction_type=construction_type,
                description=description,
                data_classification=data_classification,
                status="active",
                current_baseline_id=None,
                baseline_dirty=True,
                lock_version=1,
            )

            db.add(project)
            # autoflush=False，先写入父项目，再建立章节外键关系。
            db.flush()

            for position, (title, chapter_type) in enumerate(DEFAULT_CHAPTERS):
                chapter = Chapter(
                    id=uuid.uuid4(),
                    org_id=actor.org_id,
                    project_id=project.id,
                    parent_id=None,
                    title=title,
                    chapter_type=chapter_type,
                    position=position,
                    needs_review=True,
                    created_by=actor.user_id,
                    lock_version=1,
                )
                db.add(chapter)

            db.flush()

            return WriteResult(
                status_code=201,
                body={"data": project_summary(project), "request_id": request_id},
                resource_type="project",
                resource_id=project.id,
            )

        result = execute_idempotent(
            db,
            actor,
            key=idempotency_key,
            method="POST",
            canonical_path="/api/v1/projects",
            payload=request_data,
            action=create_project_action,
        )

        return result


# ----- 修改


def update_project(
    actor: Principal,
    session_factory: sessionmaker[Session],
    *,
    project_id: uuid.UUID,
    expected_lock_version: int,
    changes: dict[str, Any],
) -> dict[str, object]:
    """只修改已提交字段，最终由带版本号的 UPDATE 保证并发安全。"""
    with tenant_transaction(actor, session_factory) as db:
        project = require_project(
            db,
            actor,
            project_id=project_id,
        )

        authorize(actor, Permission.EDIT_CONTENT)
        require_active(project)

        # 复制路由提供的字段，不把 baseline_dirty 写回调用方的原字典。
        update_values = dict(changes)
        affects_baseline = False

        if "name" in update_values and update_values["name"] != project.name:
            affects_baseline = True

        if (
            "construction_type" in update_values
            and update_values["construction_type"] != project.construction_type
        ):
            affects_baseline = True

        if affects_baseline:
            update_values["baseline_dirty"] = True

        updated = update_project_if_version_matches(
            db,
            org_id=actor.org_id,
            project_id=project_id,
            expected_lock_version=expected_lock_version,
            changes=update_values,
        )

        if updated is None:
            raise project_update_error(
                db,
                actor,
                project_id=project_id,
            )

        if affects_baseline:
            mark_chapters_for_review(
                db,
                org_id=actor.org_id,
                project_id=project_id,
            )

        return project_summary(updated)


# ----- 归档


def ensure_no_active_runs(
    db: Session,
    actor: Principal,
    *,
    project_id: uuid.UUID,
) -> None:
    """A8 回填：检查本组织本项目是否还有非终态运行。"""
    # TODO(A8): 在同一事务、持有项目锁时查询 app.runs。
    # queued/running/waiting_input 均属非终态；存在则抛 409 RUNS_ACTIVE。
    # 创建/恢复运行也要取得同一个项目锁并重新检查 active。
    # 当前没有 runs 表，暂不检查；对应测试必须 skip，并在 PR 写明。
    return None


def archive_project(
    actor: Principal,
    session_factory: sessionmaker[Session],
    *,
    project_id: uuid.UUID,
    expected_lock_version: int,
    request_id: str,
    idempotency_key: str,
) -> WriteResult:
    """管理员归档项目，要求版本匹配并保存幂等响应。"""
    with tenant_transaction(actor, session_factory) as db:
        # 重放前也检查归属和权限，不能借缓存绕过当前身份校验。
        require_project(
            db,
            actor,
            project_id=project_id,
        )
        authorize(actor, Permission.ARCHIVE_PROJECT)

        def archive_project_action() -> WriteResult:
            """重放无需再次归档，新请求才进入这里检查状态并修改。"""
            project = db.execute(
                select(Project)
                .where(
                    Project.org_id == actor.org_id,
                    Project.id == project_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()

            if project is None:
                raise resource_not_found()

            require_active(project)
            ensure_no_active_runs(
                db,
                actor,
                project_id=project_id,
            )

            updated = update_project_if_version_matches(
                db,
                org_id=actor.org_id,
                project_id=project_id,
                expected_lock_version=expected_lock_version,
                changes={"status": "archived"},
            )

            if updated is None:
                raise project_update_error(
                    db,
                    actor,
                    project_id=project_id,
                )

            return WriteResult(
                status_code=200,
                body={"data": project_summary(updated), "request_id": request_id},
                resource_type="project",
                resource_id=project_id,
            )

        result = execute_idempotent(
            db,
            actor,
            key=idempotency_key,
            method="POST",
            canonical_path=f"/api/v1/projects/{project_id}/archive",
            payload={"expected_lock_version": expected_lock_version},
            action=archive_project_action,
        )

        return result
