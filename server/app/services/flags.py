"""Feature flag evaluation. Pure functions, no I/O — easy to mirror in client SDKs."""

import hashlib
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Op(StrEnum):
    eq = "eq"
    neq = "neq"
    in_ = "in"
    not_in = "not_in"
    gte = "gte"
    lt = "lt"
    semver_gte = "semver_gte"
    semver_lt = "semver_lt"


_LIST_OPS = {Op.in_, Op.not_in}
_NUM_OPS = {Op.gte, Op.lt}
_SEMVER_OPS = {Op.semver_gte, Op.semver_lt}
_SEMVER = re.compile(r"^\d+(\.\d+){0,2}$")


class Condition(BaseModel):
    attribute: str = Field(min_length=1, max_length=64)
    op: Op
    value: str | float | list[str]

    @model_validator(mode="after")
    def _check(self) -> "Condition":
        if (self.op in _LIST_OPS) != isinstance(self.value, list):
            raise ValueError(
                f"op {self.op} requires {'a list' if self.op in _LIST_OPS else 'a scalar'}"
            )
        if self.op in _SEMVER_OPS and not (
            isinstance(self.value, str) and _SEMVER.match(self.value)
        ):
            raise ValueError("semver ops require a version string like 1.2.3")
        if self.op in _NUM_OPS and not isinstance(self.value, int | float):
            raise ValueError("numeric ops require a number")
        return self


def _semver(v: str) -> tuple[int, int, int] | None:
    v = v.strip().lstrip("v").split("-", 1)[0].split("+", 1)[0]
    if not _SEMVER.match(v):
        return None
    parts = [int(p) for p in v.split(".")] + [0, 0]
    return parts[0], parts[1], parts[2]


def _match(cond: Condition, attrs: dict[str, str]) -> bool:
    actual = attrs.get(cond.attribute)
    if actual is None:
        # Missing attribute only satisfies negative ops.
        return cond.op in (Op.neq, Op.not_in)
    match cond.op:
        case Op.eq:
            return actual == str(cond.value)
        case Op.neq:
            return actual != str(cond.value)
        case Op.in_:
            return actual in cond.value  # type: ignore[operator]
        case Op.not_in:
            return actual not in cond.value  # type: ignore[operator]
        case Op.gte | Op.lt:
            try:
                n = float(actual)
            except ValueError:
                return False
            assert isinstance(cond.value, int | float)
            return n >= cond.value if cond.op == Op.gte else n < cond.value
        case Op.semver_gte | Op.semver_lt:
            a, b = _semver(actual), _semver(str(cond.value))
            if a is None or b is None:
                return False
            return a >= b if cond.op == Op.semver_gte else a < b
    return False  # pragma: no cover


def bucket(flag_key: str, player_id: str) -> int:
    """Stable 0..99 bucket. Salted by flag key so rollouts are independent across flags."""
    digest = hashlib.sha256(f"{flag_key}:{player_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 100


def evaluate(
    *,
    key: str,
    enabled: bool,
    rollout_percent: int,
    rules: list[dict[str, Any]],
    player_id: str,
    attrs: dict[str, str],
) -> bool:
    if not enabled:
        return False
    for raw in rules:
        if not _match(Condition.model_validate(raw), attrs):
            return False
    return bucket(key, player_id) < rollout_percent
