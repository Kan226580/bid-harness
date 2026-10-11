import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    """经过服务端认证的业务身份。"""

    org_id: uuid.UUID
    user_id: uuid.UUID
    role: str
