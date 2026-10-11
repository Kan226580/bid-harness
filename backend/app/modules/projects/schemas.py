from pydantic import BaseModel, ConfigDict, Field

from backend.app.models.projects import Project


class UpdateProjectDescriptionRequest(BaseModel):
    """A5 先实现项目 PATCH 中的描述修改。"""

    model_config = ConfigDict(extra="forbid")

    expected_lock_version: int = Field(ge=1)
    description: str = Field(max_length=2000)


def project_summary(project: Project) -> dict[str, object]:
    return {
        "id": str(project.id),
        "name": project.name,
        "construction_type": project.construction_type,
        "description": project.description,
        "data_classification": project.data_classification,
        "status": project.status,
        "current_baseline_id": (
            str(project.current_baseline_id) if project.current_baseline_id is not None else None
        ),
        "baseline_dirty": project.baseline_dirty,
        "created_by": str(project.created_by),
        "lock_version": project.lock_version,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }
