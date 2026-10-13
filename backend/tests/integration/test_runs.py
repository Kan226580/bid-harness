import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from celery import Celery
from redis import Redis
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.db_setting import BASE_DIR
from backend.app.core.principal import Principal
from backend.app.core.security import hash_password
from backend.app.core.settings import settings
from backend.app.db.tenant import tenant_transaction
from backend.app.models.idempotency import IdempotencyKey
from backend.app.models.identity.auth import Organization, User
from backend.app.models.outbox import Outbox
from backend.app.models.projects import Project
from backend.app.models.runs import Run, RunEvent, RunStep
from backend.app.modules.runs import service
from backend.app.modules.runs.domain import LeaseLost
from backend.app.modules.runs.execution import (
    Lease,
    begin_step,
    claim_run,
    complete_prepare,
    finish_demo,
    wait_for_input,
)
from backend.app.workers.dispatcher import claim_batch, finish_delivery
from backend.app.workers.recovery import sweep_org

PASSWORD = "a8-test-password-only-2026"


@dataclass(frozen=True)
class A8Data:
    actor: Principal
    project_id: uuid.UUID
    factories: dict[str, sessionmaker[Session]]

    def create(self, *, seconds: int = 3, pause: bool = False, key: str | None = None) -> uuid.UUID:
        result = service.create_demo_run(
            self.actor,
            self.factories["app"],
            project_id=self.project_id,
            seconds=seconds,
            pause=pause,
            fail_once=False,
            request_id=str(uuid.uuid4()),
            idempotency_key=key or str(uuid.uuid4()),
        )
        assert result.resource_id is not None
        return result.resource_id

    def detail(self, run_id: uuid.UUID) -> dict[str, Any]:
        return service.get_run(self.actor, self.factories["app"], run_id=run_id)


def clear_a8_data(engine: Engine) -> None:
    """测试夹具独占数据库；不使用 xdist 并行清空同一个库。"""
    name = engine.url.database
    if name is None or not name.endswith("_test"):
        raise RuntimeError("拒绝清理非测试数据库")
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE auth.organizations CASCADE"))


@pytest.fixture
def a8_data(
    engine: Engine, runtime_factories: dict[str, sessionmaker[Session]]
) -> Iterator[A8Data]:
    clear_a8_data(engine)
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    data = A8Data(Principal(org_id, user_id, "writer"), uuid.uuid4(), runtime_factories)
    try:
        with runtime_factories["auth"].begin() as db:
            db.add(Organization(id=org_id, slug="a8-test", name="A8 测试组织"))
            db.flush()
            db.add(
                User(
                    id=user_id,
                    org_id=org_id,
                    email="writer@a8.invalid",
                    display_name="测试编写人员",
                    role="writer",
                    password_hash=hash_password(PASSWORD),
                    must_change_password=False,
                )
            )
        with tenant_transaction(data.actor, runtime_factories["app"]) as db:
            db.add(
                Project(
                    id=data.project_id,
                    org_id=org_id,
                    name="A8 模拟工程",
                    construction_type="市政道路",
                    created_by=user_id,
                )
            )
        yield data
    finally:
        clear_a8_data(engine)


def table_count(data: A8Data, model: type[Any]) -> int:
    with tenant_transaction(data.actor, data.factories["app"]) as db:
        return int(db.execute(select(func.count()).select_from(model)).scalar_one())


def take_lease(data: A8Data, run_id: uuid.UUID, *, generation: int = 1) -> Lease:
    lease = claim_run(
        data.factories["app"],
        org_id=data.actor.org_id,
        run_id=run_id,
        dispatch_generation=generation,
    )
    assert lease is not None
    return lease


def complete_without_wait(data: A8Data, run_id: uuid.UUID, *, generation: int = 1) -> None:
    lease = take_lease(data, run_id, generation=generation)
    assert begin_step(data.factories["app"], lease, step_key="prepare:r0") == "execute"
    assert complete_prepare(data.factories["app"], lease)
    assert begin_step(data.factories["app"], lease, step_key="demo_work:r0") == "execute"
    assert finish_demo(data.factories["app"], lease)


def test_creation_is_atomic(a8_data: A8Data, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_after_inserts(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("故意在写好运行、步骤、事件、outbox 后失败")

    monkeypatch.setattr(service, "accepted", fail_after_inserts)
    with pytest.raises(RuntimeError):
        a8_data.create()
    for model in (Run, RunStep, RunEvent, Outbox, IdempotencyKey):
        assert table_count(a8_data, model) == 0


def test_create_request_replays_same_run(a8_data: A8Data) -> None:
    first = a8_data.create(key="a8-create-fixed-key")
    second = a8_data.create(key="a8-create-fixed-key")
    assert first == second
    assert table_count(a8_data, Run) == 1
    assert table_count(a8_data, Outbox) == 1
    assert table_count(a8_data, RunEvent) == 1
    assert table_count(a8_data, IdempotencyKey) == 1


def test_resume_keeps_id_and_snapshot(a8_data: A8Data) -> None:
    run_id = a8_data.create(pause=True)
    lease = take_lease(a8_data, run_id)
    begin_step(a8_data.factories["app"], lease, step_key="prepare:r0")
    complete_prepare(a8_data.factories["app"], lease)
    wait_for_input(a8_data.factories["app"], lease)
    before = a8_data.detail(run_id)
    result = service.control_run(
        a8_data.actor,
        a8_data.factories["app"],
        run_id=run_id,
        operation="resume",
        payload={
            "decision": "proceed_with_placeholders",
            "acknowledged_gap_ids": ["demo-confirm"],
            "note": "确认模拟缺口",
        },
        request_id=str(uuid.uuid4()),
        idempotency_key="a8-resume-fixed-key",
    )
    assert result.body["data"]["id"] == str(run_id)
    assert result.body["data"]["status"] == "queued"
    assert result.body["data"]["snapshot_hash"] == before["snapshot_hash"]
    with tenant_transaction(a8_data.actor, a8_data.factories["app"]) as db:
        run = db.get(Run, run_id)
        assert run is not None and run.lease_owner is None
        assert run.dispatch_generation == 2
    assert table_count(a8_data, Outbox) == 2


def wait_until[T](read: Callable[[], T], accept: Callable[[T], bool], *, timeout: float = 20) -> T:
    """有截止时间地轮询，不用固定等待猜测进度。"""
    deadline = time.monotonic() + timeout
    last = read()
    while not accept(last):
        if time.monotonic() >= deadline:
            raise AssertionError(f"等待超时，最后观察值：{last!r}")
        time.sleep(0.2)
        last = read()
    return last


class A8Lab:
    """子进程连接同一独立测试库，每个测试结束后先停进程再清库。"""

    def __init__(self, data: A8Data, engine: Engine, directory: Path) -> None:
        if os.name == "nt" or settings.env != "test" or settings.redis.db != 15:
            raise RuntimeError("可靠性测试需要 Linux、ENV=test、REDIS__DB=15")
        self.data = data
        self.directory = directory
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.files: list[Any] = []
        self.container_id = os.environ.get("A8_REDIS_CONTAINER_ID", "")
        if not self.container_id:
            raise RuntimeError("缺少专用 A8 Redis 容器 ID")
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{ index .Config.Labels "harness.a8-reliability" }}',
                self.container_id,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if inspected.stdout.strip() != "true":
            raise RuntimeError("Redis 容器缺少测试专用标签")
        # 测试密码为简单 ASCII；生产 Broker URL 使用 celery_app 中的 quote。
        self.broker_url = settings.redis.url
        self.redis: Redis = Redis.from_url(self.broker_url)
        self.redis.ping()
        self.redis.flushdb()
        self.producer = Celery("a8-tests", broker=self.broker_url)
        self.producer.conf.update(task_serializer="json", task_ignore_result=True)
        database_name = engine.url.database
        assert database_name is not None and database_name.endswith("_test")
        # 选择空闲端口，避免复用开发中的 API。
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.env = {
            **os.environ,
            "ENV": "test",
            "DB__NAME": database_name,
            "AUTH_DB__NAME": database_name,
            "OPS_DB__NAME": database_name,
            "SECURITY__PUBLIC_ORIGIN": self.base_url,
        }
        self.client = httpx.Client(base_url=self.base_url, timeout=20)

    def start(self, *names: str) -> None:
        commands = {
            "worker": [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "backend.app.workers.celery_app:celery_app",
                "worker",
                "--loglevel=INFO",
                "--pool=solo",
                "--concurrency=1",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
            ],
            "dispatcher": [sys.executable, "-m", "backend.app.workers.dispatcher"],
            "recovery": [sys.executable, "-m", "backend.app.workers.recovery"],
            "api": [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
        }
        for name in names:
            if name in self.processes:
                raise RuntimeError(f"{name} 已启动")
            log = (self.directory / f"{name}-{time.monotonic_ns()}.log").open("wb")
            self.files.append(log)
            self.processes[name] = subprocess.Popen(
                commands[name],
                cwd=BASE_DIR,
                env=self.env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        if "api" in names:

            def ready() -> bool:
                process = self.processes["api"]
                if process.poll() is not None:
                    raise AssertionError("测试 API 退出，请检查日志和端口")
                try:
                    return self.client.get("/health/live").status_code == 200
                except httpx.HTTPError:
                    return False

            wait_until(ready, bool)

    def kill(self, name: str) -> None:
        process = self.processes.pop(name)
        process.kill()  # Linux SIGKILL；solo 池没有执行子进程残留。
        process.wait(timeout=10)

    def wait_status(self, run_id: uuid.UUID, status: str, *, timeout: float = 30) -> dict[str, Any]:
        return wait_until(
            lambda: self.data.detail(run_id),
            lambda detail: detail["status"] == status,
            timeout=timeout,
        )

    def login(self) -> str:
        response = self.client.post(
            "/api/v1/auth/login",
            json={
                "organization_slug": "a8-test",
                "email": "writer@a8.invalid",
                "password": PASSWORD,
            },
        )
        assert response.status_code == 200
        return str(response.json()["data"]["csrf_token"])

    def send(self, run_id: uuid.UUID, *, generation: int = 1) -> None:
        self.producer.send_task(
            "harness.execute_run",
            kwargs={
                "run_id": str(run_id),
                "org_id": str(self.data.actor.org_id),
                "dispatch_generation": generation,
            },
            queue="harness_runs",
            retry=False,
        )

    def close(self) -> None:
        for name in list(self.processes):
            self.kill(name)
        self.client.close()
        self.producer.close()
        self.redis.flushdb()
        self.redis.close()
        for file in self.files:
            file.close()


@pytest.fixture
def a8_lab(a8_data: A8Data, engine: Engine, tmp_path: Path) -> Iterator[A8Lab]:
    lab = A8Lab(a8_data, engine, tmp_path)
    try:
        yield lab
    finally:
        lab.close()


def assert_one_result(data: A8Data, run_id: uuid.UUID) -> None:
    with tenant_transaction(data.actor, data.factories["app"]) as db:
        results = (
            db.execute(
                select(RunStep).where(
                    RunStep.org_id == data.actor.org_id,
                    RunStep.run_id == run_id,
                    RunStep.step_key == "demo_work:r0",
                    RunStep.status == "succeeded",
                )
            )
            .scalars()
            .all()
        )
        assert len(results) == 1
        assert results[0].result_json is not None
        assert results[0].result_json["business_result_key"] == str(run_id)
        completed = db.execute(
            select(func.count())
            .select_from(RunEvent)
            .where(
                RunEvent.org_id == data.actor.org_id,
                RunEvent.run_id == run_id,
                RunEvent.event_type == "run.succeeded",
            )
        ).scalar_one()
        assert completed == 1


@pytest.mark.reliability
def test_duplicate_outbox_delivery(a8_lab: A8Lab) -> None:
    data = a8_lab.data
    run_id = data.create()
    deliveries = claim_batch(data.factories["ops"])
    assert len(deliveries) == 1
    delivery = deliveries[0]
    # 模拟发送已成功但确认丢失：同一条协调记录发送两次。
    a8_lab.send(run_id, generation=delivery.generation)
    a8_lab.send(run_id, generation=delivery.generation)
    assert finish_delivery(data.factories["ops"], delivery, succeeded=True)
    a8_lab.start("worker")
    a8_lab.wait_status(run_id, "succeeded")
    # 再等队列消费，确认完成后的重复消息也不会产生第二份结果。
    a8_lab.send(run_id, generation=delivery.generation)
    wait_until(lambda: cast(int, a8_lab.redis.llen("harness_runs")), lambda length: length == 0)
    assert_one_result(data, run_id)
    assert table_count(data, Outbox) == 1


@pytest.mark.reliability
def test_killed_worker_recovers_after_lease_expiry(a8_lab: A8Lab) -> None:
    data = a8_lab.data
    run_id = data.create(seconds=30)
    a8_lab.start("worker", "dispatcher")
    a8_lab.wait_status(run_id, "running")
    with tenant_transaction(data.actor, data.factories["app"]) as db:
        old = db.get(Run, run_id)
        assert old is not None
        old_epoch = old.lease_epoch
    a8_lab.kill("worker")
    a8_lab.start("recovery", "worker")
    # 不修改数据库时间，不手工调用恢复；真实等待90秒租约与30秒巡检。
    a8_lab.wait_status(run_id, "succeeded", timeout=190)
    with tenant_transaction(data.actor, data.factories["app"]) as db:
        current = db.get(Run, run_id)
        assert current is not None
        assert current.recovery_count >= 1
        assert current.lease_epoch > old_epoch
    assert_one_result(data, run_id)


@pytest.mark.reliability
def test_redis_restart_and_lost_queued_message(a8_lab: A8Lab) -> None:
    data = a8_lab.data
    run_id = data.create()
    a8_lab.start("dispatcher")

    def sent() -> bool:
        with tenant_transaction(data.actor, data.factories["app"]) as db:
            return (
                db.execute(
                    select(Outbox.status).where(
                        Outbox.org_id == data.actor.org_id,
                        Outbox.run_id == run_id,
                    )
                ).scalar_one()
                == "sent"
            )

    wait_until(sent, bool)
    assert data.detail(run_id)["status"] == "queued"
    # 容器标签已由夹具校验，只操作本次测试专用 Redis。
    subprocess.run(["docker", "restart", a8_lab.container_id], check=True, capture_output=True)

    def redis_ready() -> bool:
        try:
            return bool(a8_lab.redis.ping())
        except Exception:
            return False

    wait_until(redis_ready, bool)
    # AOF 正常重启可能保留消息。额外清掉专用DB15，覆盖更强的丢消息故障。
    a8_lab.redis.flushdb()
    a8_lab.start("worker", "recovery")
    a8_lab.wait_status(run_id, "succeeded", timeout=190)
    with tenant_transaction(data.actor, data.factories["app"]) as db:
        current = db.get(Run, run_id)
        assert current is not None and current.dispatch_generation >= 2
    assert_one_result(data, run_id)


@pytest.mark.reliability
def test_running_cancel_stops_without_result(a8_lab: A8Lab) -> None:
    data = a8_lab.data
    run_id = data.create(seconds=30)
    a8_lab.start("api", "worker", "dispatcher")
    csrf = a8_lab.login()
    a8_lab.wait_status(run_id, "running")
    response = a8_lab.client.post(
        f"/api/v1/runs/{run_id}/cancel",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "a8-cancel-running"},
        json={"reason": "测试安全停止"},
    )
    assert response.status_code == 202
    assert response.json()["data"]["status"] == "running"
    assert response.json()["data"]["cancel_requested"] is True
    final = a8_lab.wait_status(run_id, "canceled")
    assert final["artifact"] is None
    with tenant_transaction(data.actor, data.factories["app"]) as db:
        step = db.execute(
            select(RunStep).where(
                RunStep.org_id == data.actor.org_id,
                RunStep.run_id == run_id,
                RunStep.step_key == "demo_work:r0",
            )
        ).scalar_one()
        assert step.result_json is None
        names = list(
            db.execute(
                select(RunEvent.event_type)
                .where(
                    RunEvent.org_id == data.actor.org_id,
                    RunEvent.run_id == run_id,
                )
                .order_by(RunEvent.seq)
            ).scalars()
        )
        assert "run.cancel_requested" in names
        assert "run.canceled" in names
        assert "run.succeeded" not in names


@pytest.mark.reliability
def test_stale_epoch_cannot_commit(a8_lab: A8Lab, engine: Engine) -> None:
    data = a8_lab.data
    run_id = data.create()
    old_lease = take_lease(data, run_id)
    # 仅在受保护的独立测试库注入过期时间，加速 fencing 边界测试。
    assert engine.url.database is not None and engine.url.database.endswith("_test")
    with engine.begin() as connection:
        connection.execute(
            text("""
            UPDATE app.runs SET lease_expires_at = clock_timestamp() - interval '1 second'
            WHERE id = :run_id
        """),
            {"run_id": run_id},
        )
    assert sweep_org(data.factories["app"], org_id=data.actor.org_id) == 1
    new_lease = take_lease(data, run_id, generation=2)
    assert new_lease.epoch > old_lease.epoch
    with pytest.raises(LeaseLost):
        finish_demo(data.factories["app"], old_lease)
    assert data.detail(run_id)["artifact"] is None
    begin_step(data.factories["app"], new_lease, step_key="prepare:r0")
    complete_prepare(data.factories["app"], new_lease)
    begin_step(data.factories["app"], new_lease, step_key="demo_work:r0")
    assert finish_demo(data.factories["app"], new_lease)
    assert_one_result(data, run_id)


def collect_sse(response: httpx.Response, *, maximum: int | None = None) -> list[int]:
    """按完整帧收集序号，忽略注释心跳；达到数量后模拟断线。"""
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    seqs: list[int] = []
    current_id: str | None = None
    for line in response.iter_lines():
        if line.startswith("id: "):
            current_id = line[4:]
        elif line == "" and current_id is not None:
            seqs.append(int(current_id.rsplit(":", 1)[1]))
            current_id = None
            if maximum is not None and len(seqs) >= maximum:
                break
    return seqs


@pytest.mark.reliability
def test_sse_disconnect_reconnect_exact_sequences(a8_lab: A8Lab) -> None:
    data = a8_lab.data
    run_id = data.create(seconds=3)
    a8_lab.start("api", "worker", "dispatcher")
    a8_lab.login()
    with a8_lab.client.stream("GET", f"/api/v1/runs/{run_id}/events") as response:
        first = collect_sse(response, maximum=2)
    assert len(first) == 2
    with a8_lab.client.stream(
        "GET",
        f"/api/v1/runs/{run_id}/events?after_seq=0",
        headers={"Last-Event-ID": f"{run_id}:{first[-1]}"},
    ) as response:
        second = collect_sse(response)
    a8_lab.wait_status(run_id, "succeeded")
    observed = first + second
    with tenant_transaction(data.actor, data.factories["app"]) as db:
        expected = list(
            db.execute(
                select(RunEvent.seq)
                .where(
                    RunEvent.org_id == data.actor.org_id,
                    RunEvent.run_id == run_id,
                )
                .order_by(RunEvent.seq)
            ).scalars()
        )
    assert observed == expected
    assert len(observed) == len(set(observed))

    def expire_for_test(engine: Engine, run_id: uuid.UUID) -> None:
        """只改变测试租约期限，其他状态仍由真实运行服务处理。"""
        assert engine.url.database is not None and engine.url.database.endswith("_test")
        with engine.begin() as connection:
            connection.execute(
                text("""
                UPDATE app.runs SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE id = :run_id
            """),
                {"run_id": run_id},
            )

    def test_recovery_limit_and_manual_retry(a8_data: A8Data, engine: Engine) -> None:
        run_id = a8_data.create()
        old_snapshot_hash = a8_data.detail(run_id)["snapshot_hash"]
        for generation in range(1, 5):
            take_lease(a8_data, run_id, generation=generation)
            expire_for_test(engine, run_id)
            recovered = sweep_org(a8_data.factories["app"], org_id=a8_data.actor.org_id)
            assert recovered == (1 if generation <= 3 else 0)
        failed = a8_data.detail(run_id)
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "LEASE_RECOVERY_EXHAUSTED"
        assert table_count(a8_data, Outbox) == 4  # 首次 + 三次自动恢复。

        first = service.control_run(
            a8_data.actor,
            a8_data.factories["app"],
            run_id=run_id,
            operation="retry",
            payload={"reason": "依赖已修复"},
            request_id=str(uuid.uuid4()),
            idempotency_key="a8-manual-retry-key",
        )
        second = service.control_run(
            a8_data.actor,
            a8_data.factories["app"],
            run_id=run_id,
            operation="retry",
            payload={"reason": "依赖已修复"},
            request_id=str(uuid.uuid4()),
            idempotency_key="a8-manual-retry-key",
        )
        assert first.body == second.body and second.replayed
        assert first.resource_id is not None and first.resource_id != run_id
        new = a8_data.detail(first.resource_id)
        assert new["retry_of_run_id"] == str(run_id)
        assert new["snapshot_hash"] == old_snapshot_hash
        assert a8_data.detail(run_id)["status"] == "failed"

    def test_queued_cancel_and_old_delivery(a8_data: A8Data) -> None:
        run_id = a8_data.create()
        result = service.control_run(
            a8_data.actor,
            a8_data.factories["app"],
            run_id=run_id,
            operation="cancel",
            payload={"reason": "还没执行，直接取消"},
            request_id=str(uuid.uuid4()),
            idempotency_key="a8-queued-cancel-key",
        )
        assert result.body["data"]["status"] == "canceled"
        assert (
            claim_run(
                a8_data.factories["app"],
                org_id=a8_data.actor.org_id,
                run_id=run_id,
                dispatch_generation=1,
            )
            is None
        )
        assert a8_data.detail(run_id)["artifact"] is None
