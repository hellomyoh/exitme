"""시세 검증 규칙 — 통과분만 적재한다 (feature-market-data §5).

규칙: low ≤ min(open, close), max(open, close) ≤ high, 가격 > 0, 거래량 ≥ 0.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationError:
    field: str
    reason: str


def validate_bar(open_: int, high: int, low: int, close: int, volume: int) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for name, v in (("open", open_), ("high", high), ("low", low), ("close", close)):
        if v <= 0:
            errors.append(ValidationError(name, "price must be > 0"))
    if volume < 0:
        errors.append(ValidationError("volume", "volume must be >= 0"))
    if errors:
        return errors
    if low > min(open_, close):
        errors.append(ValidationError("low", "low > min(open, close)"))
    if high < max(open_, close):
        errors.append(ValidationError("high", "high < max(open, close)"))
    return errors
