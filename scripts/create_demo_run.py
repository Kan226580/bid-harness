import argparse
import json
import uuid

from sqlalchemy import select

from backend.app.core.principal import Principal
from backend.app.core.settings import settings
from backend.app.db.session import AppSessionLocal, AuthSessionLocal
from backend.app.models.identity.auth import Organization, User
from backend.app.modules.operations.idempotency import validate_idempotency_key
from backend.app.modules.runs.service import create_demo_run


def main() -> None:
    """本地操作者按已有用户建立模拟运行，不提供 HTTP 注册入口。"""
    if settings.env not in {"dev", "test"}:
        raise SystemExit("模拟入口只允许 dev/test")
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--project", required=True, type=uuid.UUID)
    parser.add_argument("--key", required=True)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--pause", action="store_true")
    parser.add_argument("--fail-once", action="store_true")
    args = parser.parse_args()

    with AuthSessionLocal() as db:
        organization = db.execute(
            select(Organization).where(Organization.slug == args.org)
        ).scalar_one_or_none()
        if organization is None or not organization.is_active:
            raise SystemExit("组织不可用")
        user = db.execute(
            select(User).where(
                User.org_id == organization.id,
                User.email == args.email.strip().lower(),
            )
        ).scalar_one_or_none()
        if user is None or not user.is_active or user.must_change_password:
            raise SystemExit("用户不可用或尚未完成首次改密")
        actor = Principal(organization.id, user.id, user.role)

    key = validate_idempotency_key(args.key, required=True)
    assert key is not None
    result = create_demo_run(
        actor,
        AppSessionLocal,
        project_id=args.project,
        seconds=args.seconds,
        pause=args.pause,
        fail_once=args.fail_once,
        request_id=str(uuid.uuid4()),
        idempotency_key=key,
    )
    print(json.dumps(result.body, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
