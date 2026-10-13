# 标书 AI HARNESS AGENT

> 用于标书写作的Harness架构agent。

## 项目状态
设计完成，正在实现中。

## 简介

- 业务背景：现有工作通常涉及大量招标文件阅读、企业资料查询、历史文本复用、评分项检查和 Word 排版。
- 产品定位：面向工程技术标的、具备证据追溯与人工审核能力的 AI 撰写工作台。
- 用户定位：主要用户为施工企业的技术标编写人员、技术负责人和审核人员。
- 核心链路：招标要求 → 已确认项目事实 → 企业资料和参考资料 → 章节内容 → 审查问题 → 人工审核 → 导出草稿。
- 主要成果：可长期保存和追踪的业务成果包括要求清单、项目基准、章节版本、引用证据、审查报告、审核记录与导出快照。

## 特性

- 提速
- 提高响应质量
- 提高针对性
- 降低遗漏与误用
- 便于协作复核

## 技术栈
- 后端：Python 3.12、FastAPI、PostgreSQL、Redis、Celery
- 前端：React、TypeScript、Vite
- 部署：Docker Compose、Nginx

## 本地启动

- Docker

```bash
docker compose --env-file ./.env --env-file ./.env.admin -f deploy/compose.yaml up -d
docker compose --env-file ./.env --env-file ./.env.admin -f deploy/compose.yaml ps
docker compose --env-file ./.env --env-file ./.env.admin -f deploy/compose.yaml logs postgres
```

- 后端

```bash
uv run uvicorn backend.app.main:app --reload
```

- 前端

```bash
Set-Location D:\proj\harness\frontend
npm run dev
```

## 数据库

- 创建角色

```bash
Get-Content -LiteralPath .\scripts\sql\bootstrap_roles.sql -Raw -Encoding utf8 |
    docker compose --env-file ./.env --env-file ./.env.admin -f deploy/compose.yaml exec -T postgres psql -1 -v ON_ERROR_STOP=1 -U <ADMIN_DB__USERNAME> -d <ADMIN_DB__NAME>
```

- 管理员连接

```bash
docker compose --env-file ./.env --env-file ./.env.admin -f deploy/compose.yaml exec postgres psql -U <ADMIN_DB__USERNAME> -d <ADMIN_DB__NAME>
```

- 角色密码设置

```bash
\password migration_owner
\password app_runtime
\password auth_service
\password ops_runtime
```

- 数据库同步

```bash
uv run alembic history
uv run alembic upgrade head
uv run alembic current
```

- 演示账号

```bash
$$env:DEMO_ORG_SLUG = 'demo-construction'

$$env:DEMO_WRITER_EMAIL = 'writer@123.com'
$$env:DEMO_REVIEWER_EMAIL = 'reviewer@123.com'
$$env:DEMO_ADMIN_EMAIL = 'admin@123.com'

$$env:DEMO_WRITER_PASSWORD = Read-Host '输入演示 writer 密码（至少12位）' -MaskInput
$$env:DEMO_REVIEWER_PASSWORD = Read-Host '输入演示 reviewer 密码（至少12位）' -MaskInput
$$env:DEMO_ADMIN_PASSWORD = Read-Host '输入演示 admin 密码（至少12位）' -MaskInput

$$env:DEMO_MUTE_CHANGE_PASSWORD = 'true'

uv run python -m scripts.create_demo_data

Remove-Item Env:\DEMO_WRITER_PASSWORD,Env:\DEMO_REVIEWER_PASSWORD
```

## 测试

- 后端

```bash
# 如果本地 postgre 占用 5432 端口，需要先停止服务
uv run pytest
```

- 前端

```bash
cd ./frontend

$$env:E2E_ORG_SLUG = 'demo-construction'
$$env:E2E_ADMIN_EMAIL = Read-Host '输入本地测试管理员邮箱'
$$env:E2E_ADMIN_PASSWORD = Read-Host '输入已经改过的管理员密码' -MaskInput
npm run test:e2e
Remove-Item Env:\E2E_ADMIN_PASSWORD

cd ../
```

## 代码质检

- 后端

```bash
uv run ruff check --fix .
uv run ruff format .
uv run ruff format --check .
uv run mypy
```

- 前端

```bash
cd ./frontend

npm run format
npm run format:check
npm run lint
npm run typecheck
npm run build

cd ../
```