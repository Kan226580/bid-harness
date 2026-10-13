from urllib.parse import quote

from celery import Celery

from backend.app.core.settings import settings

password = quote(settings.redis.hashed_password.get_secret_value(), safe="")
broker_url = f"redis://:{password}@{settings.redis.host}:{settings.redis.port}/{settings.redis.db}"

celery_app = Celery(
    "harness",
    broker=broker_url,
    include=["backend.app.workers.tasks"],
)
celery_app.conf.update(
    task_default_queue="harness_runs",
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_store_errors_even_if_ignore=False,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=None,
    broker_connection_timeout=5,
    broker_transport_options={
        "visibility_timeout": 3600,
        "socket_connect_timeout": 5,
        "socket_timeout": 5,
    },
    timezone="UTC",
    enable_utc=True,
)
