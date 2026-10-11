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

- FastAPI

```bash
uv run uvicorn backend.app.main:app --reload
```

## 代码质检

- CI

```bash
uv run ruff check --fix .
uv run ruff format .
uv run ruff format --check .
uv run mypy
```