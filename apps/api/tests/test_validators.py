"""OHLC 검증 규칙 테스트 — feature-market-data.md §12."""
from app.services.validators import validate_bar


def test_valid_bar_passes():
    assert validate_bar(100, 110, 95, 105, 1000) == []


def test_low_above_open_close_rejected():
    errors = validate_bar(100, 110, 101, 105, 1000)
    assert any(e.field == "low" for e in errors)


def test_high_below_open_close_rejected():
    errors = validate_bar(100, 104, 95, 105, 1000)
    assert any(e.field == "high" for e in errors)


def test_zero_price_rejected():
    # KRX가 거래정지일에 시가 0을 반환하는 케이스
    errors = validate_bar(0, 110, 95, 105, 1000)
    assert any(e.reason == "price must be > 0" for e in errors)


def test_negative_volume_rejected():
    errors = validate_bar(100, 110, 95, 105, -1)
    assert any(e.field == "volume" for e in errors)


def test_flat_bar_passes():
    # 동시호가만 체결된 날: O=H=L=C
    assert validate_bar(100, 100, 100, 100, 10) == []
