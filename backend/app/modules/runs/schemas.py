import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RunKind = Literal["parse", "index", "analyze", "generate", "review", "export"]
RunState = Literal[
    "queued", "running", "waiting_input", "retry_wait", "succeeded", "failed", "canceled"
]


class CancelRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500, description="取消原因")


class RetryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500, description="人工重试原因")


class ResumeRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["proceed_with_placeholders", "cancel"] = Field(..., description="恢复决定")
    acknowledged_gap_ids: list[str] = Field(..., max_length=100, description="已确定的缺口编号")
    note: str = Field(..., min_length=1, max_length=1000, description="人工处理说明")


class BudgetResponse(BaseModel):
    max_calls: int = Field(..., ge=0, description="最大模型调用数")
    max_tokens: int = Field(..., ge=0, description="最大 Token 数")
    max_cost_cny: str | None = Field(..., description="费用上限，十进制字符串")


class UsageResponse(BaseModel):
    call_count: int = Field(..., ge=0, description="已开始的模型调用数")
    tokens_observed: int = Field(..., ge=0, description="已观察 Token 数")
    tokens_reserved: int = Field(..., ge=0, description="预留 Token 数")
    cost_observed_cny: str = Field(..., description="已观察费用")
    cost_reserved_cny: str = Field(..., description="预留费用")
    has_unknown_calls: bool = Field(..., description="是否存在费用未知的调用")


class ArtifactResponse(BaseModel):
    type: Literal[
        "document_version", "analysis_batch", "chapter_version", "review_report", "export"
    ] = Field(..., description="成果类型")
    id: uuid.UUID = Field(..., description="成果编号")


class RunErrorResponse(BaseModel):
    code: str = Field(..., description="后台错误码")
    message: str = Field(..., description="脱敏错误摘要")
    retryable: bool = Field(..., description="错误是否可恢复")


class RunResponse(BaseModel):
    id: uuid.UUID = Field(..., description="运行编号")
    project_id: uuid.UUID | None = Field(..., description="归属项目")
    kind: RunKind = Field(..., description="任务类型")
    chapter_id: uuid.UUID | None = Field(..., description="目标章节")
    status: RunState = Field(..., description="运行状态")
    cancel_requested: bool = Field(..., description="是否已请求取消")
    current_step: str | None = Field(..., description="当前阶段")
    completed_steps: int = Field(..., ge=0, description="已成功完成的步骤数")
    total_steps: int = Field(..., ge=0, description="已登记步骤数")
    baseline_id: uuid.UUID | None = Field(..., description="冻结基准")
    snapshot_hash: str = Field(..., description="冻结输入哈希")
    model_mode: Literal["none", "cloud", "simulated"] = Field(..., description="执行模式")
    retry_of_run_id: uuid.UUID | None = Field(..., description="人工重试的原运行")
    budget: BudgetResponse = Field(..., description="运行预算")
    usage: UsageResponse = Field(..., description="运行用量")
    input_required: dict[str, Any] | None = Field(..., description="当前需要的人工输入")
    artifact: ArtifactResponse | None = Field(..., description="已提交成果")
    error: RunErrorResponse | None = Field(..., description="后台错误")
    last_event_seq: int = Field(..., ge=0, description="最新持久化事件序号")
    created_at: datetime = Field(..., description="创建时间")
    started_at: datetime | None = Field(..., description="首次开始时间")
    finished_at: datetime | None = Field(..., description="终态时间")


class RunAcceptedResponse(BaseModel):
    run_id: uuid.UUID = Field(..., description="新运行编号")
    status: RunState = Field(..., description="当前运行状态")
    retry_of_run_id: uuid.UUID | None = Field(default=None, description="原失败运行")
    events_url: str = Field(..., description="事件流相对路径")
    status_url: str = Field(..., description="详情相对路径")
