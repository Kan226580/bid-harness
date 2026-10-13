FROM python:3.12-slim
WORKDIR /workspace
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
COPY backend ./backend
ENV PATH="/workspace/.venv/bin:$PATH"
CMD ["python", "-m", "celery", "-A", "backend.app.workers.celery_app:celery_app", "worker", "--loglevel=INFO", "--pool=threads", "--concurrency=2"]