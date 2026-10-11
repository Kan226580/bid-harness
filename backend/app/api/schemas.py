from pydantic import BaseModel, Field


class SuccessResponse[T](BaseModel):
    """统一成功响应：具体业务数据和请求编号。"""

    data: T = Field(
        ...,
        description="本次请求的业务数据，具体结构由接口的响应模型决定",
    )
    request_id: str = Field(
        ...,
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        description="请求 UUID；幂等重放时保留首次响应中的编号，本次编号见 X-Request-ID",
    )


class CursorPage[T](BaseModel):
    """游标分页结果：当前页数据、下一页游标和是否还有数据。"""

    items: list[T] = Field(
        ...,
        max_length=100,
        description="当前页的数据列表，空结果返回空数组，每页最多 100 条",
    )
    next_cursor: str | None = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="下一页游标；没有下一页时必须返回 null，不能省略字段",
    )
    has_more: bool = Field(
        ...,
        strict=True,
        description="是否还有下一页；true 时 next_cursor 应提供下一页游标",
    )
