import pytest

from backend.app.modules.runs.domain import InvalidTransition, transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("queued", "running"),
        ("queued", "canceled"),
        ("queued", "failed"),
        ("running", "waiting_input"),
        ("running", "retry_wait"),
        ("running", "succeeded"),
        ("running", "failed"),
        ("running", "canceled"),
        ("running", "queued"),
        ("waiting_input", "queued"),
        ("waiting_input", "canceled"),
        ("retry_wait", "queued"),
        ("retry_wait", "canceled"),
        ("retry_wait", "failed"),
    ],
)
def test_legal_transition(current: str, target: str) -> None:
    assert transition(current, target) == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("succeeded", "running"),
        ("failed", "queued"),
        ("canceled", "running"),
        ("queued", "succeeded"),
        ("waiting_input", "succeeded"),
        ("running", "running"),
        ("unknown", "running"),
    ],
)
def test_illegal_transition(current: str, target: str) -> None:
    with pytest.raises(InvalidTransition):
        transition(current, target)
