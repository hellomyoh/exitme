"""KIS 클라이언트 테스트 — HTTP는 responses 로 mock (키 불필요).

검증: 토큰 발급·캐시, 공통 헤더, TR 치환 규칙, 일봉 페이지네이션, rt_cd 오류, 로그 마스킹.
"""
from datetime import date, datetime, timedelta

import responses

from app.services.kis_auth import BASE_URLS, KisAuth, mask
from app.services.kis_client import DAILY_CHART_PATH, PRICE_PATH, KisClient, KisError

PROD = BASE_URLS["prod"]


def token_response(expired_in_hours: int = 24) -> dict:
    expired = (datetime.now() + timedelta(hours=expired_in_hours)).strftime("%Y-%m-%d %H:%M:%S")
    return {"access_token": "test-token", "access_token_token_expired": expired}


@responses.activate
def test_token_issued_once_and_cached():
    responses.post(PROD + "/oauth2/tokenP", json=token_response())
    auth = KisAuth("key", "secret", "prod")
    assert auth.access_token() == "test-token"
    assert auth.access_token() == "test-token"  # 캐시 재사용
    assert len(responses.calls) == 1


@responses.activate
def test_headers_contain_required_fields():
    responses.post(PROD + "/oauth2/tokenP", json=token_response())
    auth = KisAuth("key", "secret", "prod")
    h = auth.headers("FHKST03010100")
    assert h["authorization"] == "Bearer test-token"
    assert h["appkey"] == "key"
    assert h["tr_id"] == "FHKST03010100"
    assert h["custtype"] == "P"


@responses.activate
def test_vps_substitutes_order_tr_only():
    responses.post(BASE_URLS["vps"] + "/oauth2/tokenP", json=token_response())
    auth = KisAuth("key", "secret", "vps")
    assert auth.headers("TTTC0802U")["tr_id"] == "VTTC0802U"  # 주문 TR → V 치환
    assert auth.headers("FHKST03010100")["tr_id"] == "FHKST03010100"  # 시세 TR 유지


def daily_body(rows: list[dict]) -> dict:
    return {"rt_cd": "0", "msg1": "OK", "output1": {}, "output2": rows}


def row(d: str, o: int, h: int, l: int, c: int, v: int) -> dict:
    return {
        "stck_bsop_date": d,
        "stck_oprc": str(o),
        "stck_hgpr": str(h),
        "stck_lwpr": str(l),
        "stck_clpr": str(c),
        "acml_vol": str(v),
    }


@responses.activate
def test_fetch_daily_parses_and_sorts():
    responses.post(PROD + "/oauth2/tokenP", json=token_response())
    responses.get(
        PROD + DAILY_CHART_PATH,
        json=daily_body([row("20260827", 70000, 70500, 69500, 70000, 100), row("20260826", 69000, 69900, 68800, 69500, 90)]),
    )
    client = KisClient(KisAuth("key", "secret", "prod"))
    bars = client.fetch_daily("069500", date(2026, 8, 26), date(2026, 8, 27))
    assert [b.trade_date for b in bars] == [date(2026, 8, 26), date(2026, 8, 27)]  # 오름차순
    assert bars[1].close == 70000 and bars[0].volume == 90


@responses.activate
def test_fetch_daily_paginates_windows():
    """구간이 140일을 넘으면 창을 나눠 여러 번 호출한다 (호출당 최대 100건 제한 대응)."""
    responses.post(PROD + "/oauth2/tokenP", json=token_response())
    responses.get(PROD + DAILY_CHART_PATH, json=daily_body([]))
    client = KisClient(KisAuth("key", "secret", "prod"))
    client.fetch_daily("069500", date(2025, 1, 1), date(2025, 12, 31))
    chart_calls = [c for c in responses.calls if DAILY_CHART_PATH in c.request.url]
    assert len(chart_calls) == 3  # 365일 / 140일 창 = 3회
    # 요청 파라미터에 원주가(FID_ORG_ADJ_PRC=1) — 원본 보존 원칙
    assert "FID_ORG_ADJ_PRC=1" in chart_calls[0].request.url


@responses.activate
def test_rt_cd_error_raises():
    responses.post(PROD + "/oauth2/tokenP", json=token_response())
    responses.get(PROD + DAILY_CHART_PATH, json={"rt_cd": "1", "msg1": "invalid code"})
    client = KisClient(KisAuth("key", "secret", "prod"))
    try:
        client.fetch_daily("999999", date(2026, 8, 27), date(2026, 8, 27))
        raise AssertionError("expected KisError")
    except KisError as e:
        assert "invalid code" in str(e)


@responses.activate
def test_fetch_price():
    responses.post(PROD + "/oauth2/tokenP", json=token_response())
    responses.get(
        PROD + PRICE_PATH,
        json={"rt_cd": "0", "output": {"stck_prpr": "70000", "prdy_vrss": "500", "acml_vol": "12345"}},
    )
    client = KisClient(KisAuth("key", "secret", "prod"))
    out = client.fetch_price("069500")
    assert out["stck_prpr"] == "70000"


def test_mask_hides_secret():
    assert "PSabcdefghijk" not in mask("PSabcdefghijklmn")
    assert mask("short") == "****"
