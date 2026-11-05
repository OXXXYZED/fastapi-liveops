import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.services.flags import Condition, bucket, evaluate


def _eval(rules: list[dict[str, Any]], attrs: dict[str, str], pct: int = 100) -> bool:
    return evaluate(
        key="f", enabled=True, rollout_percent=pct, rules=rules, player_id="p", attrs=attrs
    )


def test_disabled_is_false() -> None:
    assert not evaluate(
        key="f", enabled=False, rollout_percent=100, rules=[], player_id="p", attrs={}
    )


@pytest.mark.parametrize(
    ("rule", "attrs", "expected"),
    [
        ({"attribute": "platform", "op": "eq", "value": "ios"}, {"platform": "ios"}, True),
        ({"attribute": "platform", "op": "eq", "value": "ios"}, {"platform": "android"}, False),
        ({"attribute": "platform", "op": "eq", "value": "ios"}, {}, False),
        ({"attribute": "platform", "op": "neq", "value": "ios"}, {}, True),
        ({"attribute": "country", "op": "in", "value": ["US", "CA"]}, {"country": "CA"}, True),
        ({"attribute": "country", "op": "not_in", "value": ["US"]}, {"country": "DE"}, True),
        ({"attribute": "level", "op": "gte", "value": 10}, {"level": "12"}, True),
        ({"attribute": "level", "op": "lt", "value": 10}, {"level": "abc"}, False),
        ({"attribute": "v", "op": "semver_gte", "value": "1.10.0"}, {"v": "1.9.9"}, False),
        ({"attribute": "v", "op": "semver_gte", "value": "1.10.0"}, {"v": "v1.10.0-beta"}, True),
        ({"attribute": "v", "op": "semver_lt", "value": "2"}, {"v": "1.99"}, True),
        ({"attribute": "v", "op": "semver_lt", "value": "2"}, {"v": "garbage"}, False),
    ],
)
def test_conditions(rule: dict[str, object], attrs: dict[str, str], expected: bool) -> None:
    assert _eval([rule], attrs) is expected


def test_all_rules_must_match() -> None:
    rules = [
        {"attribute": "platform", "op": "eq", "value": "ios"},
        {"attribute": "country", "op": "eq", "value": "US"},
    ]
    assert _eval(rules, {"platform": "ios", "country": "US"})
    assert not _eval(rules, {"platform": "ios", "country": "DE"})


@pytest.mark.parametrize(
    "bad",
    [
        {"attribute": "a", "op": "in", "value": "x"},
        {"attribute": "a", "op": "eq", "value": ["x"]},
        {"attribute": "a", "op": "semver_gte", "value": "abc"},
        {"attribute": "a", "op": "gte", "value": "10"},
        {"attribute": "a", "op": "regex", "value": "x"},
    ],
)
def test_invalid_conditions(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Condition.model_validate(bad)


def test_bucket_is_stable_and_roughly_uniform() -> None:
    assert bucket("f", "p") == bucket("f", "p")
    players = [str(uuid.UUID(int=i)) for i in range(10_000)]
    hits = sum(bucket("flag", p) < 25 for p in players)
    assert 2300 < hits < 2700
    # Different flags give independent buckets.
    overlap = sum(bucket("a", p) < 50 and bucket("b", p) < 50 for p in players)
    assert 2300 < overlap < 2700


def test_rollout_zero_and_hundred() -> None:
    assert not _eval([], {}, pct=0)
    assert _eval([], {}, pct=100)
