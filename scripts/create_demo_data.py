"""
演示数据

$env:DEMO_WRITER_PASSWORD = "换成长密码至少12位"
$env:DEMO_REVIEWER_PASSWORD = "再换一个长密码至少12位"
$env:DEMO_WRITER_EMAIL = "writer@example.invalid"
$env:DEMO_REVIEWER_EMAIL = "reviewer@example.invalid"

uv run python -m scripts.create_demo_data
"""

import os
import re
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from backend.app.core.security import hash_password
from backend.app.db.session import AuthSessionLocal
from backend.app.models import Organization, User

MIN_PASSWORD_LENGTH = 12
SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"缺少环境变量 {name}")
    return value


def upsert_user(
    db: DbSession,
    organization: Organization,
    *,
    email: str,
    display_name: str,
    role: str,
    password: str,
    must_change_password: bool,
) -> None:
    user = db.execute(
        select(User).where(User.org_id == organization.id, User.email == email.lower())
    ).scalar_one_or_none()
    if user is None:
        user = User(
            org_id=organization.id,
            email=email.lower(),
            display_name=display_name,
            password_hash=hash_password(password),
            role=role,
            must_change_password=must_change_password,
        )
        db.add(user)
        print(f"创建账号：{email} （{role}）")
    else:
        user.password_hash = hash_password(password)
        user.display_name = display_name
        user.role = role
        user.must_change_password = must_change_password
        user.failed_login_count = 0
        user.locked_until = None
        print(f"更新账号：{email} （{role}）")


def main() -> int:
    slug = os.environ.get("DEMO_ORG_SLUG", "demo-construction").strip()
    name = os.environ.get("DEMO_ORG_NAME", "演示建筑公司").strip()
    if not SLUG_PATTERN.fullmatch(slug):
        raise SystemExit("DEMO_ORG_SLUG 只能用小写字母、数字、连字符")

    writer_email = require_env("DEMO_WRITER_EMAIL")
    reviewer_email = require_env("DEMO_REVIEWER_EMAIL")
    writer_password = require_env("DEMO_WRITER_PASSWORD")
    reviewer_password = require_env("DEMO_REVIEWER_PASSWORD")
    must_change = os.environ.get("DEMO_MUTE_CHANGE_PASSWORD", "true").lower() in {
        "1",
        "true",
        "yes",
    }

    for env_name, password in (
        ("DEMO_WRITER_PASSWORD", writer_password),
        ("DEMO_REVIEWER_PASSWORD", reviewer_password),
    ):
        if len(password) < MIN_PASSWORD_LENGTH:
            raise SystemExit(f"{env_name} 太短，至少 {MIN_PASSWORD_LENGTH} 个字符")
        if password in {writer_email, reviewer_email}:
            raise SystemExit("密码不能和邮箱相同")

    with AuthSessionLocal() as db:
        organization = db.execute(
            select(Organization).where(Organization.slug == slug)
        ).scalar_one_or_none()
        if organization is None:
            organization = Organization(slug=slug, name=name)
            db.add(organization)
            db.flush()
            print(f"创建组织：{slug}")
        else:
            organization.name = name
            print(f"组织已存在，更新名称：{slug}")

        upsert_user(
            db,
            organization,
            email=writer_email,
            display_name="演示编写者",
            role="writer",
            password=writer_password,
            must_change_password=must_change,
        )
        upsert_user(
            db,
            organization,
            email=reviewer_email,
            display_name="演示审核者",
            role="reviewer",
            password=reviewer_password,
            must_change_password=must_change,
        )
        db.commit()

    print("----- 完成，密码没有打印也没写入任何文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
