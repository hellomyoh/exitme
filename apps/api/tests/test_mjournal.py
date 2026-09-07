"""수동 매매일지 — FIFO 계산·요율·소유 격리 (2026-09-05)."""
import uuid

from fastapi.testclient import TestClient

from app.main import app


def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"mj{uuid.uuid4().hex[:8]}@x.dev",
                                         "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


_CORE = ("symbol", "qty", "avg_price", "cost", "realized", "matched", "return_pct")


def _core(holdings):
    """보유 항목의 FIFO 핵심 키만 — 현재가 평가 필드(code·price·eval…)는 별도 테스트에서 검증 (2026-09-06)."""
    return [{k: h[k] for k in _CORE} for h in holdings]


def test_journal_fifo_and_summary():
    c, h = _client()
    jid = c.post("/mjournals", json={"name": "대원제약 스윙", "symbol": "대원제약",
                                     "broker": "NH", "fee_rate": 0.001, "tax_rate": 0.002},
                 headers=h).json()["id"]
    # 매수 10@10,000(1/5) → 매수 10@12,000(1/10) → 매도 15@13,000(2/1)
    for side, qty, price, d in [("buy", 10, 10000, "2026-01-05"), ("buy", 10, 12000, "2026-01-10"),
                                ("sell", 15, 13000, "2026-02-01")]:
        r = c.post(f"/mjournals/{jid}/entries",
                   json={"side": side, "qty": qty, "price": price, "trade_date": d}, headers=h)
        assert r.status_code == 201, r.text
    d = c.get(f"/mjournals/{jid}", headers=h).json()
    sell = next(r for r in d["rows"] if r["side"] == "sell")
    # FIFO 원가 = 10×10,000 + 5×12,000 = 160,000 / 매도금 195,000 / 매도비용 = 195,000×0.003 = 585
    assert sell["amount"] == 195000 and sell["cost"] == 585
    assert sell["realized"] == 195000 - 585 - 160000
    assert sell["buy_date"] == "2026-01-05" and sell["hold_days"] == 27
    assert abs(sell["return_pct"] - sell["realized"] / 160000) < 1e-9
    s = d["summary"]
    assert s["buy_amount"] == 220000 and s["sell_amount"] == 195000
    assert s["cost"] == round(100000 * 0.001) + round(120000 * 0.001) + 585
    assert s["realized"] == sell["realized"]
    assert _core(d["holdings"]) == [{"symbol": "대원제약", "qty": 5, "avg_price": 12000, "cost": 60000,
                              "realized": 34415, "matched": 160000, "return_pct": 34415 / 160000}]
    assert s["return_pct"] == 34415 / 160000  # 요약 수익률 = 실현손익 ÷ 매도분 원가 (2026-09-05)

    # 보유 초과 매도 거부
    r = c.post(f"/mjournals/{jid}/entries",
               json={"side": "sell", "qty": 99, "price": 13000, "trade_date": "2026-02-02"}, headers=h)
    assert r.status_code == 422


def test_journal_isolation_and_delete():
    c, h = _client()
    jid = c.post("/mjournals", json={"name": "A", "symbol": "X"}, headers=h).json()["id"]
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 1, "price": 100}, headers=h)
    _, h2 = _client()
    assert c.get(f"/mjournals/{jid}", headers=h2).status_code == 404
    assert c.delete(f"/mjournals/{jid}", headers=h2).status_code == 404
    assert c.get("/mjournals", headers=h2).json()["items"] == []
    # 소유자 삭제 → CASCADE
    assert c.delete(f"/mjournals/{jid}", headers=h).json()["deleted"] is True
    assert c.get("/mjournals", headers=h).json()["items"] == []


def test_multi_symbol_fifo_isolation():
    """다종목 (0015): 종목별 FIFO 독립·종목별 보유 초과 매도 거부·종목별 누적 실현 시리즈."""
    c, h = _client()
    jid = c.post("/mjournals", json={"name": "멀티", "symbol": "대원제약",
                                     "fee_rate": 0.0, "tax_rate": 0.0}, headers=h).json()["id"]
    E = lambda b: c.post(f"/mjournals/{jid}/entries", json=b, headers=h)
    assert E({"side": "buy", "qty": 10, "price": 1000, "trade_date": "2026-01-01"}).status_code == 201  # 기본 종목
    assert E({"side": "buy", "qty": 5, "price": 2000, "trade_date": "2026-01-02", "symbol": "휴메딕스"}).status_code == 201
    # 휴메딕스 보유 5 — 대원제약 10주가 있어도 휴메딕스 6주 매도는 거부 (종목별 검사)
    assert E({"side": "sell", "qty": 6, "price": 2100, "trade_date": "2026-01-03", "symbol": "휴메딕스"}).status_code == 422
    assert E({"side": "sell", "qty": 5, "price": 2100, "trade_date": "2026-01-03", "symbol": "휴메딕스"}).status_code == 201
    d = c.get(f"/mjournals/{jid}", headers=h).json()
    sell = next(r for r in d["rows"] if r["side"] == "sell")
    assert sell["symbol"] == "휴메딕스" and sell["realized"] == 5 * (2100 - 2000)  # 대원제약 로트와 안 섞임
    assert _core(d["holdings"]) == [{"symbol": "대원제약", "qty": 10, "avg_price": 1000, "cost": 10000,
                              "realized": 0, "matched": 0, "return_pct": None}]
    assert set(d["symbols"]) == {"대원제약", "휴메딕스"}
    assert d["series"] == {"휴메딕스": [{"date": "2026-01-03", "value": 500}]}  # 매도 없는 종목은 라인 없음


def test_journal_isolation_between_journals_and_users():
    """일지 간·계정 간 완전 분리 (2026-09-05 지시): 새 일지에는 다른 일지의 종목·보유·추이가 섞이지 않고,
    전 일지 합산 엔드포인트도 없다. 다른 계정은 일지 자체를 볼 수 없다."""
    c, h = _client()
    a = c.post("/mjournals", json={"name": "A", "symbol": "tiger 200", "fee_rate": 0.0, "tax_rate": 0.0},
               headers=h).json()["id"]
    c.post(f"/mjournals/{a}/entries", json={"side": "buy", "qty": 10, "price": 1000, "trade_date": "2026-01-01"}, headers=h)
    c.post(f"/mjournals/{a}/entries", json={"side": "sell", "qty": 4, "price": 1500, "trade_date": "2026-01-02"}, headers=h)
    b = c.post("/mjournals", json={"name": "B", "symbol": "kodex 200"}, headers=h).json()["id"]
    db = c.get(f"/mjournals/{b}", headers=h).json()
    assert db["symbols"] == ["kodex 200"] and db["holdings"] == [] and db["series"] == {} and db["rows"] == []
    assert c.get("/mjournals/overview", headers=h).status_code in (404, 422)  # 합산 뷰 제거

    c2, h2 = _client()  # 다른 계정
    assert c2.get(f"/mjournals/{a}", headers=h2).status_code == 404
    assert c2.get("/mjournals", headers=h2).json()["items"] == []
    assert c2.post(f"/mjournals/{a}/entries", json={"side": "buy", "qty": 1, "price": 1}, headers=h2).status_code == 404


def test_journal_broker_link_and_import(monkeypatch):
    """일지 ↔ 계좌 연결 + 체결 가져오기 (0018): 종목명 정규화 매칭·새 종목 추가·보유 초과/수동 중복 경고·멱등·격리."""
    from datetime import date as _d

    from app.services import kis_client
    from app.services.kis_client import Execution

    c, h = _client()
    jid = c.post("/mjournals", json={"name": "연금저축", "symbol": "kodex 200", "fee_rate": 0.0, "tax_rate": 0.0},
                 headers=h).json()["id"]
    # 수동 기초 보유 10주
    assert c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 10, "price": 10000,
                                                     "trade_date": "2026-01-02"}, headers=h).status_code == 201
    # 연결 전 가져오기는 409
    assert c.post(f"/mjournals/{jid}/import-fills", headers=h).status_code == 409

    acct = c.post("/broker/accounts", json={"label": "연금", "app_key": "PS" + "k" * 34, "app_secret": "S" * 180,
                                            "account_no": "10040029-22"}, headers=h).json()
    assert c.get(f"/mjournals/{jid}/broker", headers=h).json() == {"linked": False, "account": None}
    r = c.put(f"/mjournals/{jid}/broker", json={"credential_id": acct["id"]}, headers=h).json()
    assert r["linked"] is True and r["account"]["label"] == "연금" and r["account"]["account_no"] == "1004**29-22"
    assert c.get(f"/mjournals/{jid}", headers=h).json()["linked_account"]["id"] == acct["id"]
    # 다른 계정: 일지도 계좌도 보이지 않는다
    c2, h2 = _client()
    assert c2.put(f"/mjournals/{jid}/broker", json={"credential_id": acct["id"]}, headers=h2).status_code == 404
    j2 = c2.post("/mjournals", json={"name": "남의 일지", "symbol": "x"}, headers=h2).json()["id"]
    assert c2.put(f"/mjournals/{j2}/broker", json={"credential_id": acct["id"]}, headers=h2).status_code == 404

    def ex(no, d, code, side, qty, price, name):
        return Execution(order_no=no, trade_date=d, code=code, side=side, filled_qty=qty, avg_price=price,
                         order_qty=qty, remain_qty=0, name=name)

    execs = [
        ex("A1", _d(2026, 1, 5), "069500", "buy", 5, 11000, "KODEX 200"),    # 이름 매칭(대소문자·공백 무시) → 코드 학습
        ex("A2", _d(2026, 1, 6), "102110", "buy", 3, 20000, "TIGER 200"),    # 새 종목
        ex("A3", _d(2026, 1, 7), "069500", "sell", 30, 12000, "KODEX 200"),  # 보유 25 초과 → 경고(등록은 됨)
        ex("A4", _d(2026, 1, 2), "069500", "buy", 10, 10000, "KODEX 200"),   # 수동 기록과 동일 → 중복 경고
    ]

    class _Fake:
        def __init__(self, *a, **kw):
            pass

        def fetch_executions(self, start, end, only_filled=True):
            return execs

    monkeypatch.setattr(kis_client, "KisTradingClient", _Fake)

    pv = c.post(f"/mjournals/{jid}/import-fills?days=30", headers=h).json()
    assert pv["dry_run"] is True and pv["fetched"] == 4 and pv["added"] == 0
    by = {i["broker_ref"]: i for i in pv["items"]}
    assert by["A4:2026-01-02"]["symbol"] == "kodex 200" and by["A4:2026-01-02"]["match"] == "이름"
    assert by["A1:2026-01-05"]["symbol"] == "kodex 200" and by["A1:2026-01-05"]["match"] == "코드"
    assert by["A2:2026-01-06"]["symbol"] == "TIGER 200" and by["A2:2026-01-06"]["match"] == "새 종목"
    assert any("보유(25주)보다 많은 매도" in w for w in by["A3:2026-01-07"]["warnings"])
    assert any("수동 기록" in w for w in by["A4:2026-01-02"]["warnings"])
    assert pv["new_symbols"] == ["TIGER 200"]
    assert len(c.get(f"/mjournals/{jid}", headers=h).json()["rows"]) == 1  # 미리보기는 저장하지 않는다

    ap = c.post(f"/mjournals/{jid}/import-fills?days=30&dry_run=false", headers=h).json()
    assert ap["added"] == 4 and ap["skipped"] == 0
    d = c.get(f"/mjournals/{jid}", headers=h).json()
    assert len(d["rows"]) == 5 and sum(r["source"] == "broker" for r in d["rows"]) == 4
    assert set(d["symbols"]) == {"kodex 200", "TIGER 200"}
    over = next(r for r in d["rows"] if r["side"] == "sell")
    assert "많은 매도" in (over.get("error") or "") and over["code"] == "069500"  # 경고 행으로 표시(자동 수정 없음)

    again = c.post(f"/mjournals/{jid}/import-fills?days=30&dry_run=false", headers=h).json()
    assert again["added"] == 0 and again["skipped"] == 4                          # 재실행 멱등
    assert len(c.get(f"/mjournals/{jid}", headers=h).json()["rows"]) == 5

    c.put(f"/mjournals/{jid}/broker", json={"credential_id": None}, headers=h)   # 해제
    assert c.get(f"/mjournals/{jid}/broker", headers=h).json()["linked"] is False
    assert c.post(f"/mjournals/{jid}/import-fills", headers=h).status_code == 409



def test_journal_close_reopen_and_dashboard_assets():
    """청산(0020): 청산 일지는 기록 추가 409·대시보드 매매일지 자산·총자산에서 제외, 다시 열기로 복구.
    대시보드는 주식 거래 자산(trading_total)과 매매일지 자산(journal)을 분리해 내려준다."""
    c, h = _client()
    a = c.post("/mjournals", json={"name": "연금", "symbol": "kodex 200", "fee_rate": 0.0, "tax_rate": 0.0}, headers=h).json()["id"]
    b = c.post("/mjournals", json={"name": "정리끝", "symbol": "tiger 200", "fee_rate": 0.0, "tax_rate": 0.0}, headers=h).json()["id"]
    c.post(f"/mjournals/{a}/entries", json={"side": "buy", "qty": 10, "price": 100000, "trade_date": "2026-01-02"}, headers=h)
    c.post(f"/mjournals/{b}/entries", json={"side": "buy", "qty": 5, "price": 50000, "trade_date": "2026-01-02"}, headers=h)
    c.post(f"/mjournals/{b}/entries", json={"side": "sell", "qty": 5, "price": 60000, "trade_date": "2026-01-03"}, headers=h)

    d = c.get("/dashboard", headers=h).json()
    # 2026-09-07 이름 매칭 도입 후 'kodex 200' 은 시세가 있으면 평가액으로 잡힌다 —
    # 이 테스트의 관심사는 청산/복구와 자산 분리이므로 '합계 = 집계 대상 일지 값의 합' 불변식으로 검증한다.
    j_sum = sum(x["value"] for x in d["journals"] if x["counted"])
    assert d["journal"] == j_sum and d["trading_total"] == d["stock"] + d["cash"]
    assert d["total"] == d["stock"] + d["cash"] + d["other"] + d["journal"]
    assert {j["name"] for j in d["journals"]} == {"연금", "정리끝"}
    done = next(j for j in d["journals"] if j["name"] == "정리끝")
    assert done["cost"] == 0 and done["realized"] == 50_000 and done["counted"] is True

    # 청산 → 대시보드에서 사라짐, 기록 추가 거절, 목록/상세에 closed_at
    r = c.post(f"/mjournals/{b}/close", headers=h).json()
    assert r["closed_at"] and r["warning"] is None            # 보유 없음 → 경고 없음
    assert c.post(f"/mjournals/{b}/entries", json={"side": "buy", "qty": 1, "price": 1}, headers=h).status_code == 409
    d2 = c.get("/dashboard", headers=h).json()
    assert {j["name"] for j in d2["journals"]} == {"연금"} and d2["journal"] == j_sum
    assert next(i for i in c.get("/mjournals", headers=h).json()["items"] if i["id"] == b)["closed_at"]
    assert c.get(f"/mjournals/{b}", headers=h).json()["closed_at"]
    # 보유가 남은 일지를 청산하면 경고 문구
    assert "보유 잔여" in (c.post(f"/mjournals/{a}/close", headers=h).json()["warning"] or "")
    assert c.get("/dashboard", headers=h).json()["journal"] == 0
    # 다시 열기
    assert c.post(f"/mjournals/{a}/reopen", headers=h).json()["closed_at"] is None
    assert c.get("/dashboard", headers=h).json()["journal"] == j_sum
    assert c.post(f"/mjournals/{a}/entries", json={"side": "buy", "qty": 1, "price": 1000}, headers=h).status_code == 201


def test_journal_same_account_as_portfolio_dedupes_by_instrument():
    """같은 증권사 계좌를 실전매매 포트도 쓰면 **포트가 실제 보유한 종목만** 일지에서 빼고 나머지는 총자산에 넣는다
    (2026-09-06: 계좌 단위로 통째로 빼던 규칙이 현금만 있는 포트 때문에 일지 주식 전부를 누락시켰다)."""
    c, h = _client()
    acct = c.post("/broker/accounts", json={"label": "연금", "app_key": "PS" + "k" * 34, "app_secret": "S" * 180,
                                            "account_no": "10040029-22"}, headers=h).json()
    pid = c.post("/portfolios", json={"name": "연금 포트"}, headers=h).json()["id"]
    c.put(f"/portfolio/{pid}/broker", json={"credential_id": acct["id"]}, headers=h)
    # 포트는 현금만 (입금) — 일지의 주식과 겹치는 종목 없음
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 3_000_000, "executed_at": "2026-01-02T15:30:00+09:00"}, headers=h)
    jid = c.post("/mjournals", json={"name": "연금 일지", "symbol": "kodex 200", "fee_rate": 0.0, "tax_rate": 0.0}, headers=h).json()["id"]
    c.put(f"/mjournals/{jid}/broker", json={"credential_id": acct["id"]}, headers=h)
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 2, "price": 100000, "trade_date": "2026-01-02"}, headers=h)
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 3, "price": 50000, "symbol": "기타주", "trade_date": "2026-01-03"}, headers=h)
    d = c.get("/dashboard", headers=h).json()
    j = next(x for x in d["journals"] if x["id"] == jid)
    assert j["counted"] is True and j["note"] is None and j["excluded"] == [] and j["value"] == 350_000
    assert d["journal"] == 350_000 and d["cash"] == 3_000_000   # 일지 주식 + 포트 현금 — 둘 다 총자산에

    # 포트가 같은 종목(KODEX 200, 069500)을 실제 보유하면 그 종목만 일지에서 빠진다 (이름 매칭 — 일지 기록엔 코드 없음)
    c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500", "qty": 5, "price": 100000,
                               "executed_at": "2026-01-05T15:30:00+09:00"}, headers=h)
    d2 = c.get("/dashboard", headers=h).json()
    j2 = next(x for x in d2["journals"] if x["id"] == jid)
    assert j2["counted"] is True and j2["value"] == 150_000                       # 기타주만 남음
    assert [x["symbol"] for x in j2["excluded"]] == ["kodex 200"] and "제외" in j2["note"]
    assert d2["journal"] == 150_000

    # 일지 종목이 전부 겹치면 counted=False, 총자산에는 실전매매 쪽만
    c.post(f"/mjournals/{jid}/entries", json={"side": "sell", "qty": 3, "price": 51000, "symbol": "기타주", "trade_date": "2026-01-06"}, headers=h)
    d3 = c.get("/dashboard", headers=h).json()
    j3 = next(x for x in d3["journals"] if x["id"] == jid)
    assert j3["counted"] is False and "실전매매 쪽만 포함" in j3["note"] and d3["journal"] == 0


def test_reset_assets_wipes_everything_but_account():
    """계정 자산 전체 초기화 (2026-09-05 지시): 포트·일지·기타 자산·스냅샷 삭제, 아이디 확인 불일치는 400, 계좌·자격은 유지."""
    import uuid as _u

    c = TestClient(app, base_url="https://testserver")
    email = f"rs{_u.uuid4().hex[:8]}@x.dev"
    tok = c.post("/auth/register", json={"email": email, "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    pid = c.post("/portfolios", json={"name": "포트"}, headers=h).json()["id"]
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 1_000_000,
                               "executed_at": "2026-01-02T10:00:00+09:00"}, headers=h)
    jid = c.post("/mjournals", json={"name": "일지", "symbol": "x"}, headers=h).json()["id"]
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 1, "price": 1000}, headers=h)
    acct = c.post("/broker/accounts", json={"label": "계좌", "app_key": "PS" + "k" * 34, "app_secret": "S" * 180,
                                            "account_no": "12345678-01"}, headers=h).json()
    c.put(f"/portfolio/{pid}/broker", json={"credential_id": acct["id"]}, headers=h)
    assert c.get("/dashboard", headers=h).status_code == 200  # 스냅샷 생성

    assert c.post("/account/reset-assets", json={"confirm": "wrong"}, headers=h).status_code == 400
    assert c.post("/account/reset-assets", json={"confirm": email, "scopes": []}, headers=h).status_code == 422
    out = c.post("/account/reset-assets", json={"confirm": email}, headers=h).json()
    assert out["reset"] is True and out["deleted"]["portfolios"] >= 1 and out["deleted"]["journals"] == 1
    assert out["deleted"]["snapshots"] >= 1
    assert c.get("/portfolios", headers=h).json()["items"] == []
    assert c.get("/mjournals", headers=h).json()["items"] == []
    assert c.get(f"/portfolio/summary?portfolio_id={pid}", headers=h).status_code == 404
    # 계정·증권사 계좌 자격은 남는다
    assert c.get("/auth/me", headers=h).status_code == 200
    assert len(c.get("/broker/accounts", headers=h).json()["items"]) == 1
    # 이후 대시보드는 빈 상태로 다시 시작
    d = c.get("/dashboard", headers=h).json()
    assert d["total"] == 0 and d["journals"] == [] and (d.get("portfolios") or []) == []



def test_journal_import_holdings_from_balance(monkeypatch):
    """잔고 기초 보유 등록 (2026-09-05): 체결 기간 이전에 산 보유분을 잔고로 대조해 부족분만 등록, 같은 날 재실행은 멱등."""
    from app.services import kis_client

    c, h = _client()
    jid = c.post("/mjournals", json={"name": "한투-삼성", "symbol": "삼성전자", "fee_rate": 0.0, "tax_rate": 0.0},
                 headers=h).json()["id"]
    # 일지에는 삼성전자 3주만 기록돼 있고, 계좌 잔고는 11주 + KODEX 200 4주
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 3, "price": 70000, "trade_date": "2026-01-02"}, headers=h)
    acct = c.post("/broker/accounts", json={"label": "위탁", "app_key": "PS" + "k" * 34, "app_secret": "S" * 180,
                                            "account_no": "68800037-01"}, headers=h).json()
    assert c.get(f"/mjournals/{jid}/broker-holdings", headers=h).status_code == 409   # 연결 전
    c.put(f"/mjournals/{jid}/broker", json={"credential_id": acct["id"]}, headers=h)

    class _Fake:
        def __init__(self, *a, **kw):
            pass

        def fetch_holdings(self):
            return [{"code": "005930", "name": "삼성전자", "qty": 11, "avg_price": 72150, "buy_amount": 793650,
                     "price": 75000, "eval_amount": 825000},
                    {"code": "069500", "name": "KODEX 200", "qty": 4, "avg_price": 108555, "buy_amount": 434220,
                     "price": 105720, "eval_amount": 422880}]

    monkeypatch.setattr(kis_client, "KisTradingClient", _Fake)
    hv = c.get(f"/mjournals/{jid}/broker-holdings", headers=h).json()
    by = {i["code"]: i for i in hv["items"]}
    assert by["005930"]["symbol"] == "삼성전자" and by["005930"]["match"] == "이름"
    assert by["005930"]["journal_qty"] == 3 and by["005930"]["diff"] == 8          # 11 − 3
    assert by["069500"]["match"] == "새 종목" and by["069500"]["diff"] == 4

    r = c.post(f"/mjournals/{jid}/import-holdings", json={"items": [
        {"code": "005930", "name": "삼성전자", "qty": 8, "price": 72150}], "trade_date": "2026-09-05"}, headers=h)
    assert r.status_code == 201 and r.json()["added"] == 1
    d = c.get(f"/mjournals/{jid}", headers=h).json()
    assert _core(d["holdings"]) == [{"symbol": "삼성전자", "qty": 11, "avg_price": round((3 * 70000 + 8 * 72150) / 11),
                              "cost": 3 * 70000 + 8 * 72150, "realized": 0, "matched": 0, "return_pct": None}]
    added = next(x for x in d["rows"] if x["qty"] == 8)
    assert added["source"] == "broker" and added["code"] == "005930" and added["reason"] == "증권사 잔고 기초 보유"
    # 다시 대조하면 부족분 0, 같은 날 재등록은 건너뜀
    assert {i["code"]: i["diff"] for i in c.get(f"/mjournals/{jid}/broker-holdings", headers=h).json()["items"]}["005930"] == 0
    again = c.post(f"/mjournals/{jid}/import-holdings", json={"items": [
        {"code": "005930", "name": "삼성전자", "qty": 8, "price": 72150}], "trade_date": "2026-09-05"}, headers=h).json()
    assert again["added"] == 0 and again["skipped"] == 1
    # 청산 일지는 거절
    c.post(f"/mjournals/{jid}/close", headers=h)
    assert c.post(f"/mjournals/{jid}/import-holdings", json={"items": [{"code": "069500", "qty": 4, "price": 108555}]},
                  headers=h).status_code == 409



def test_journal_valuation_from_broker_prices(monkeypatch):
    """평가손익 (2026-09-06): 연결 계좌 잔고의 현재가로 보유를 평가 — 종목별 평가액·평가손익·수익률, 합계·총손익,
    대시보드 매매일지 자산은 평가액. 시세가 없는 일지는 None/취득원가 유지."""
    import app.mjournal as mj
    from app.services import kis_client

    mj._PRICE_CACHE.clear()
    c, h = _client()
    jid = c.post("/mjournals", json={"name": "한투-삼성", "symbol": "삼성전자", "fee_rate": 0.0, "tax_rate": 0.0},
                 headers=h).json()["id"]
    acct = c.post("/broker/accounts", json={"label": "위탁", "app_key": "PS" + "q" * 34, "app_secret": "S" * 180,
                                            "account_no": "68800037-01"}, headers=h).json()
    c.put(f"/mjournals/{jid}/broker", json={"credential_id": acct["id"]}, headers=h)

    class _Fake:
        def __init__(self, *a, **kw):
            pass

        def fetch_holdings(self):
            return [{"code": "005930", "name": "삼성전자", "qty": 11, "avg_price": 138200, "buy_amount": 1520200,
                     "price": 255500, "eval_amount": 2810500}]

    monkeypatch.setattr(kis_client, "KisTradingClient", _Fake)
    # 잔고 기초 보유 11주 @138,200 (코드 있음) + 코드 없는 수동 종목 1건(시세 없음 → None)
    c.post(f"/mjournals/{jid}/import-holdings", json={"items": [{"code": "005930", "name": "삼성전자", "qty": 11, "price": 138200}],
                                                    "trade_date": "2026-09-05"}, headers=h)
    d = c.get(f"/mjournals/{jid}", headers=h).json()
    s1 = next(x for x in d["holdings"] if x["symbol"] == "삼성전자")
    assert s1["price"] == 255500 and s1["price_source"] == "증권사 잔고" and s1["code"] == "005930"
    assert s1["eval"] == 11 * 255500 and s1["unrealized"] == 11 * (255500 - 138200) == 1_290_300
    assert abs(s1["unrealized_pct"] - 1_290_300 / 1_520_200) < 1e-9
    assert d["summary"]["priced"] is True and d["summary"]["eval_total"] == 2_810_500
    assert d["summary"]["unrealized_total"] == 1_290_300 and d["summary"]["total_pnl"] == 1_290_300

    dash = c.get("/dashboard", headers=h).json()
    ja = next(x for x in dash["journals"] if x["id"] == jid)
    assert ja["priced"] is True and ja["value"] == 2_810_500 and ja["unrealized"] == 1_290_300 and ja["cost"] == 1_520_200
    assert dash["journal"] == 2_810_500  # 총자산에는 평가액

    # 시세 없는 종목이 섞이면 priced=False, 그 종목은 None, 총자산 합산은 취득원가로 보호
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 2, "price": 50000, "symbol": "미상장주", "trade_date": "2026-09-01"}, headers=h)
    d2 = c.get(f"/mjournals/{jid}", headers=h).json()
    unk = next(x for x in d2["holdings"] if x["symbol"] == "미상장주")
    assert unk["price"] is None and unk["eval"] is None and d2["summary"]["priced"] is False and d2["summary"]["priced_count"] == 1
    assert c.get("/dashboard", headers=h).json()["journal"] == 1_520_200 + 100_000


def _seed_bars(code: str, name: str, start: str, n: int, base: int, step: int):
    """합성 일봉 — 평일만, 종가 = base + i*step. 반환: {date: close}"""
    from datetime import date, timedelta

    from app.db import SessionLocal
    from app.services.ingest import get_or_create_instrument, upsert_daily_bars

    closes = {}
    d = date.fromisoformat(start)
    rows = []
    i = 0
    while len(rows) < n:
        if d.weekday() < 5:
            c = base + i * step
            rows.append({"trade_date": d, "open": c, "high": c + 10, "low": c - 10, "close": c, "volume": 100})
            closes[d.isoformat()] = c
            i += 1
        d += timedelta(days=1)
    with SessionLocal() as s:
        inst = get_or_create_instrument(s, code, name, "KOSPI", type_="STOCK")
        upsert_daily_bars(s, inst.id, rows, source="pykrx")
        s.commit()
    return closes


def test_journal_return_series_fifo_avg_and_segments(monkeypatch):
    """보유 평단 대비 일별 수익률 (2026-09-06): 평단은 FIFO 잔여 로트 기준으로 추가 매수·부분 매도 날부터 바뀌고,
    청산 종목은 보유 구간만 그려지며, 시세 키가 없으면 DB 일봉만으로 그리고 안내를 남긴다."""
    import app.mjournal as mj

    monkeypatch.setattr(mj, "_kis_for_bars", lambda session, j: None)   # 네트워크 없음 — DB 일봉만
    mj._PRICE_CACHE.clear()
    ca = _seed_bars("990001", "테스트A", "2026-01-02", 70, 10000, 10)
    cb = _seed_bars("990002", "테스트B", "2026-01-02", 70, 5000, 5)
    c, h = _client()
    jid = c.post("/mjournals", json={"name": "수익률", "symbol": "테스트A", "fee_rate": 0.0, "tax_rate": 0.0}, headers=h).json()["id"]
    for side, qty, price, d, sym, code in [
        ("buy", 10, 10000, "2026-01-05", "테스트A", "990001"), ("buy", 10, 12000, "2026-01-20", "테스트A", "990001"),
        ("sell", 15, 13000, "2026-02-10", "테스트A", "990001"),
        ("buy", 4, 5000, "2026-01-05", "테스트B", "990002"), ("sell", 4, 5100, "2026-01-15", "테스트B", "990002"),
    ]:
        r = c.post(f"/mjournals/{jid}/entries", json={"side": side, "qty": qty, "price": price, "trade_date": d,
                                                    "symbol": sym, "code": code}, headers=h)
        assert r.status_code == 201, r.text
    res = c.get(f"/mjournals/{jid}/return-series", headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    a = body["symbols"]["테스트A"]
    assert a["held"] is True and a["qty"] == 5 and a["avg"] == 12000     # FIFO: 10@10,000 전부 + 5@12,000 매도 → 5@12,000 남음
    assert a["since"] == "2026-01-20"                                      # 남은 로트의 매수일 = 보유 시작일
    pts = {p["date"]: p for p in a["segments"][0]}
    assert "2026-01-02" not in pts and pts["2026-01-05"]["avg"] == 10000        # 첫 매수일부터
    assert abs(pts["2026-01-05"]["pct"] - (ca["2026-01-05"] / 10000 - 1)) < 1e-9
    assert pts["2026-01-19"]["avg"] == 10000 and pts["2026-01-20"]["avg"] == 11000  # 추가 매수 당일부터 평단 11,000
    assert abs(pts["2026-01-20"]["pct"] - (ca["2026-01-20"] / 11000 - 1)) < 1e-9
    assert pts["2026-02-09"]["avg"] == 11000 and pts["2026-02-10"]["avg"] == 12000 and pts["2026-02-10"]["qty"] == 5
    assert len(a["segments"]) == 1 and a["current_pct"] == a["segments"][0][-1]["pct"]  # 현재가 없음 → 마지막 종가
    b = body["symbols"]["테스트B"]
    assert b["held"] is False and len(b["segments"]) == 1
    bd = [p["date"] for p in b["segments"][0]]
    assert bd[0] == "2026-01-05" and bd[-1] == "2026-01-14" and "2026-01-15" not in bd  # 매도일부터는 점 없음
    # 종합: B 청산 후(1/15~)는 A 만 → A 와 같은 값; 두 종목 보유 중(1/5~1/14)엔 가중 합
    tot = {p["date"]: p["pct"] for p in body["total"]}
    assert abs(tot["2026-02-10"] - pts["2026-02-10"]["pct"]) < 1e-9
    exp = (10 * ca["2026-01-06"] + 4 * cb["2026-01-06"]) / (10 * 10000 + 4 * 5000) - 1
    assert abs(tot["2026-01-06"] - exp) < 1e-9
    assert body["priced"] is True and any("시세 키" in n for n in body["notes"])  # 오늘까지 봉이 없어 보충 시도 → 키 없음 안내


def test_journal_return_series_fetches_missing_bars(monkeypatch):
    """DB 에 일봉이 없는 종목은 KIS 일봉으로 받아 적재하고(Instrument 생성), 이후 호출은 DB 만 쓴다."""
    import app.mjournal as mj
    from app.services.kis_client import DailyBar
    from datetime import date, timedelta

    calls = []

    class _FakeKis:
        def fetch_daily(self, code, start, end):
            calls.append((code, start, end))
            out, d = [], date(2026, 3, 2)
            while d <= min(end, date(2026, 3, 31)):
                if d.weekday() < 5:
                    c = 20000 + d.day * 10
                    out.append(DailyBar(trade_date=d, open=20000, high=c + 100, low=19900, close=c, volume=1))
                d += timedelta(days=1)
            return out

    monkeypatch.setattr(mj, "_kis_for_bars", lambda session, j: _FakeKis())
    mj._PRICE_CACHE.clear()
    c, h = _client()
    jid = c.post("/mjournals", json={"name": "보충", "symbol": "새종목", "fee_rate": 0.0, "tax_rate": 0.0}, headers=h).json()["id"]
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 3, "price": 20000, "trade_date": "2026-03-03",
                                             "symbol": "새종목", "code": "990003"}, headers=h)
    body = c.get(f"/mjournals/{jid}/return-series", headers=h).json()
    assert calls and calls[0][0] == "990003"
    seg = body["symbols"]["새종목"]["segments"][0]
    assert seg[0]["date"] == "2026-03-03" and abs(seg[0]["pct"] - (20030 / 20000 - 1)) < 1e-9
    n_calls = len(calls)
    body2 = c.get(f"/mjournals/{jid}/return-series", headers=h).json()
    # 두 번째 호출: 3월 봉은 있고 꼬리(4월~오늘)만 부족 → 꼬리 구간 1회만 더 조회(가짜 클라이언트는 3월 이후 빈 응답)
    assert len(calls) == n_calls + 1 and calls[-1][1] >= date(2026, 3, 31)
    assert body2["symbols"]["새종목"]["segments"][0][0]["date"] == "2026-03-03"


def test_journal_account_total_only_when_journal_covers_account(monkeypatch):
    """계좌 평가금액 (2026-09-06 지시): 주식 평가액 + 예수금. 일지가 계좌 주식을 전부 담고 있을 때만 계산하고,
    대시보드 총자산에는 예수금을 넣지 않는다(한 계좌를 여러 일지에 연결하면 중복되므로)."""
    import app.mjournal as mj
    from app.services import kis_client

    mj._PRICE_CACHE.clear()
    c, h = _client()
    jid = c.post("/mjournals", json={"name": "한투", "symbol": "삼성전자", "fee_rate": 0.0, "tax_rate": 0.0},
                 headers=h).json()["id"]
    acct = c.post("/broker/accounts", json={"label": "위탁", "app_key": "PS" + "a" * 34, "app_secret": "S" * 180,
                                            "account_no": "68800037-01"}, headers=h).json()
    c.put(f"/mjournals/{jid}/broker", json={"credential_id": acct["id"]}, headers=h)

    class _Fake:
        def __init__(self, *a, **kw):
            pass

        def fetch_balance(self):
            return {"holdings": [{"code": "005930", "name": "삼성전자", "qty": 11, "avg_price": 138200,
                                  "buy_amount": 1520200, "price": 255500, "eval_amount": 2810500}],
                    "deposit": 480000, "total_eval": 3290500}

        def fetch_holdings(self):
            return self.fetch_balance()["holdings"]

    monkeypatch.setattr(kis_client, "KisTradingClient", _Fake)
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 11, "price": 138200, "code": "005930",
                                              "trade_date": "2026-01-08"}, headers=h)
    d = c.get(f"/mjournals/{jid}", headers=h).json()["summary"]
    assert d["account_covered"] is True and d["account_deposit"] == 480000
    assert d["account_total"] == 2_810_500 + 480_000          # 주식 평가 + 예수금
    # 대시보드 총자산은 예수금을 빼고 주식 평가액만
    dash = c.get("/dashboard", headers=h).json()
    assert dash["journal"] == 2_810_500 and next(x for x in dash["journals"] if x["id"] == jid)["value"] == 2_810_500

    # 계좌에 없는 종목이 일지에 섞이면 커버리지 실패 → 표시하지 않는다
    mj._PRICE_CACHE.clear()
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 2, "price": 50000, "symbol": "미상장주",
                                              "trade_date": "2026-02-01"}, headers=h)
    d2 = c.get(f"/mjournals/{jid}", headers=h).json()["summary"]
    assert d2["account_covered"] is False and d2["account_total"] is None and d2["account_deposit"] == 480000

    # 수량이 어긋나도(계좌 11주 vs 일지 9주) 커버리지 실패
    mj._PRICE_CACHE.clear()
    c2, h2 = _client()
    j2 = c2.post("/mjournals", json={"name": "부분", "symbol": "삼성전자", "fee_rate": 0.0, "tax_rate": 0.0},
                 headers=h2).json()["id"]
    a2 = c2.post("/broker/accounts", json={"label": "위탁2", "app_key": "PS" + "b" * 34, "app_secret": "S" * 180,
                                           "account_no": "68800037-01"}, headers=h2).json()
    c2.put(f"/mjournals/{j2}/broker", json={"credential_id": a2["id"]}, headers=h2)
    c2.post(f"/mjournals/{j2}/entries", json={"side": "buy", "qty": 9, "price": 138200, "code": "005930",
                                              "trade_date": "2026-01-08"}, headers=h2)
    s2 = c2.get(f"/mjournals/{j2}", headers=h2).json()["summary"]
    assert s2["account_covered"] is False and s2["account_total"] is None


def test_valuation_price_coverage_and_backfill(monkeypatch):
    """다종목 평가 커버리지 (2026-09-07 지시 ①+③): DB 미적재 종목은 KIS 일봉으로 보충,
    코드 없는 행은 종목명으로 instruments 매칭, 끝내 못 구한 종목은 summary.unpriced 로 드러난다.

    회귀 대상: 시세를 못 구한 종목이 보유수익률 분모에서 조용히 빠지던 결함(2026-09-07 재현).
    """
    from datetime import date as _date

    import app.mjournal as mj
    from app.db import SessionLocal
    from app.services.ingest import get_or_create_instrument, upsert_daily_bars

    mj._PRICE_CACHE.clear()
    mj._CLOSE_MISS.clear()

    # ① DB 에 적재된 종목 ② 이름만 아는 종목(코드 미입력) — 둘 다 instruments 에 존재
    with SessionLocal() as s:
        for code, name, close in (("102110", "TIGER 200", 100_000), ("069500", "KODEX 200", 30_000)):
            inst = get_or_create_instrument(s, code, name, "KOSPI")
            upsert_daily_bars(s, inst.id, [{"trade_date": _date(2026, 9, 4), "open": close, "high": close,
                                            "low": close, "close": close, "volume": 1}], source="kis")
        s.commit()

    # ③ DB 에 없는 종목 — KIS 일봉 보충 경로가 채운다
    from app.services.kis_client import DailyBar

    class _Fake:
        def fetch_daily(self, code, a, b, org_price=True):
            return [DailyBar(_date(2026, 9, 4), 80_000, 80_000, 80_000, 80_000, 1)] if code == "005930" else []

    monkeypatch.setattr(mj, "_kis_for_bars", lambda session, j: _Fake())

    c, h = _client()
    jid = c.post("/mjournals", json={"name": "커버리지", "symbol": "TIGER 200", "fee_rate": 0.0, "tax_rate": 0.0},
                 headers=h).json()["id"]
    for sym, code, qty, price in (("TIGER 200", "102110", 10, 90_000), ("삼성전자", "005930", 10, 70_000),
                                  ("KODEX 200", None, 10, 25_000), ("듣보종목", None, 10, 50_000)):
        body = {"side": "buy", "qty": qty, "price": price, "trade_date": "2026-09-01", "symbol": sym}
        if code:
            body["code"] = code
        assert c.post(f"/mjournals/{jid}/entries", json=body, headers=h).status_code == 201

    s = c.get(f"/mjournals/{jid}", headers=h).json()["summary"]
    assert s["holdings_count"] == 4 and s["priced_count"] == 3 and s["priced"] is False
    assert s["cost_total"] == 2_350_000            # 전체 원가
    assert s["cost_priced"] == 1_850_000           # 시세 있는 3종목만이 수익률 분모
    assert [u["symbol"] for u in s["unpriced"]] == ["듣보종목"]   # 조용한 제외 금지
    assert s["unpriced"][0]["cost"] == 500_000
    # 평가 = 10×100,000 + 10×80,000(KIS 보충) + 10×30,000(이름 매칭) = 2,100,000
    assert s["eval_total"] == 2_100_000
    assert abs(s["unrealized_pct"] - 250_000 / 1_850_000) < 1e-9


def test_return_series_resolves_code_by_name(monkeypatch):
    """수익률 차트 코드 해석 (2026-09-07): 코드 미입력 종목도 이름 매칭으로 라인이 그려진다.

    회귀 대상: 평가(도넛·카드)에는 뜨는 종목이 차트에서만 '코드 없음'으로 빠지던 화면 간 불일치.
    """
    from datetime import date as _date

    import app.mjournal as mj
    from app.db import SessionLocal
    from app.services.ingest import get_or_create_instrument, upsert_daily_bars

    mj._PRICE_CACHE.clear()
    mj._CLOSE_MISS.clear()
    with SessionLocal() as s:
        inst = get_or_create_instrument(s, "005930", "삼성전자", "KOSPI", type_="STOCK")
        upsert_daily_bars(s, inst.id, [{"trade_date": _date(2026, 9, d), "open": 80_000, "high": 80_000,
                                        "low": 80_000, "close": 80_000 + d * 100, "volume": 1}
                                       for d in (1, 2, 3, 4)], source="kis")
        s.commit()
    monkeypatch.setattr(mj, "_kis_for_bars", lambda session, j: None)  # DB 만으로 충분

    c, h = _client()
    jid = c.post("/mjournals", json={"name": "이름만", "symbol": "삼성전자", "fee_rate": 0.0, "tax_rate": 0.0},
                 headers=h).json()["id"]
    # 코드 없이 종목명만 입력
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 10, "price": 70_000,
                                              "trade_date": "2026-09-01"}, headers=h)
    rs = c.get(f"/mjournals/{jid}/return-series", headers=h).json()
    sym = rs["symbols"].get("삼성전자")
    assert sym and sym["code"] == "005930", "이름 매칭으로 코드가 붙어야 한다"
    assert sym["segments"] and sym["segments"][0], "수익률 라인 구간이 그려져야 한다"
    assert rs["priced"] is True
    assert not any("시세를 붙일 수 없는" in n for n in rs["notes"])
