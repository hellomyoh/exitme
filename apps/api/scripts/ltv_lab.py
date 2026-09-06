"""LTV 연구용 백테스트 러너 (2026-09-06) — DB 일봉으로 변형들을 한 번에 돌려 표로 낸다.

사용: docker compose exec -T api python -m scripts.ltv_lab [--pair QQQ:QLD|QQQ:TQQQ|379800:225040] [--from 2010-01-01] [--to 2026-12-31]
      [--is-end 2018-12-31]   # IS/OOS 분리 보고
      [--json out.json]
리포지토리 코드는 바꾸지 않는다 — 결과는 THROUGHLINE/docs/ltv-strategy-study-20260906.md 에 기록.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Instrument, OhlcvDaily
from app.strategy.ltv import LTVParams, LTVResult, buy_and_hold, run_ltv

FEES = {"QQQ": 0.0020, "QLD": 0.0095, "TQQQ": 0.0084, "379800": 0.0009, "225040": 0.0025, "360750": 0.0007,
        "069500": 0.0015, "102110": 0.0015, "122630": 0.0064}
MULT = {"QLD": 2.0, "TQQQ": 3.0, "225040": 2.0, "122630": 2.0}
KR_CODES = {"069500", "102110", "122630", "379800", "225040", "360750"}
KR_COMMISSION, KR_SLIPPAGE = 0.00015, 0.0005   # 국내 온라인 수수료 0.015% (RAVG 엔진 기본과 동일) + 슬리피지


def load(session, code: str, d0: date, d1: date) -> dict[str, dict]:
    inst = session.scalar(select(Instrument).where(Instrument.code == code))
    if inst is None:
        raise SystemExit(f"instrument {code} not seeded")
    rows = session.execute(select(OhlcvDaily).where(OhlcvDaily.instrument_id == inst.id,
                                                    OhlcvDaily.trade_date >= d0, OhlcvDaily.trade_date <= d1)
                           .order_by(OhlcvDaily.trade_date)).scalars().all()
    return {r.trade_date.isoformat(): {"date": r.trade_date.isoformat(), "open": float(r.open_raw) * float(r.adj_factor),
                                      "high": float(r.high_raw) * float(r.adj_factor), "low": float(r.low_raw) * float(r.adj_factor),
                                      "close": float(r.close_raw) * float(r.adj_factor)} for r in rows}


def align(a: dict, b: dict | None):
    if b is None:
        ds = sorted(a)
        return [a[d] for d in ds], None
    ds = sorted(set(a) & set(b))
    return [a[d] for d in ds], [b[d] for d in ds]


def fmt(r: LTVResult) -> dict:
    k = r.kpi
    return {"label": r.label, "total": k["total_return"], "cagr": k["cagr"], "mdd": k["mdd"], "sharpe": k["sharpe"],
            "calmar": (k["cagr"] / abs(k["mdd"])) if (k["cagr"] is not None and k["mdd"] < 0) else None,
            "flips": r.flips, "rebal": r.rebalances, "turnover": r.turnover, "tim": r.time_in_market, "avg_e": r.avg_exposure,
            "days": len(r.equity)}


def row(d: dict) -> str:
    def pct(x):
        return "—" if x is None else f"{x * 100:+.1f}%"
    def num(x, f="{:.2f}"):
        return "—" if x is None else f.format(x)
    return (f"| {d['label']} | {pct(d['total'])} | {pct(d['cagr'])} | {pct(d['mdd'])} | {num(d['sharpe'])} | "
            f"{num(d['calmar'])} | {d['flips']} | {d['rebal']} | {num(d['turnover'], '{:.1f}')} | {pct(d['tim'])} | {num(d['avg_e'])} |")


HEADER = ("| 변형 | 총수익 | CAGR | MDD | 샤프 | 칼마 | 추세전환 | 체결일 | 회전율/년 | 시장체류 | 평균E |\n"
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")


def variants(lev_mult: float, has_lev: bool) -> list[tuple[str, LTVParams]]:
    base = LTVParams(lev_multiple=lev_mult)
    out = [
        ("TF-1x (MA200, 이탈2%)", replace(base, sigma_target=None, e_max=1.0)),
    ]
    if has_lev:
        out += [
            ("LRS 고정 2x", replace(base, sigma_target=None, e_max=2.0)),
            ("VT σ20% 1~2x", replace(base, sigma_target=0.20, e_floor=1.0, e_max=2.0)),
            ("VT σ25% 1~2x", replace(base, sigma_target=0.25, e_floor=1.0, e_max=2.0)),
            ("VT σ30% 1~2x", replace(base, sigma_target=0.30, e_floor=1.0, e_max=2.0)),
            ("VT σ25% 0.5~2x", replace(base, sigma_target=0.25, e_floor=0.5, e_max=2.0)),
            ("VT σ25% 1~2x + σcap35%", replace(base, sigma_target=0.25, e_floor=1.0, e_max=2.0, sigma_cap=0.35)),
            ("VT σ25% 1~2x + 12M모멘텀", replace(base, sigma_target=0.25, e_floor=1.0, e_max=2.0, mom_filter=252)),
            ("VT σ25% 1~2x 월말판정", replace(base, sigma_target=0.25, e_floor=1.0, e_max=2.0, monthly_only=True)),
            ("VT σ25% 1~2x MA150", replace(base, sigma_target=0.25, e_floor=1.0, e_max=2.0, ma_len=150)),
            ("VT σ25% 1~2x MA250", replace(base, sigma_target=0.25, e_floor=1.0, e_max=2.0, ma_len=250)),
            ("VT σ25% 1~2x 이탈1%", replace(base, sigma_target=0.25, e_floor=1.0, e_max=2.0, exit_buffer=0.01)),
            ("VT σ25% 1~2x 이탈3%", replace(base, sigma_target=0.25, e_floor=1.0, e_max=2.0, exit_buffer=0.03)),
        ]
        # ── 2차(빠른 게이트 MA50·고점대비·빠른σ) 전부 기각. 3차: 급락 서킷브레이커 유효(2020 낙폭 −45→−32%).
        # ── 4차: 요동 구간(2015-07~2016-07, 2011, 2018Q4)에서만 레버리지를 끄는 거리·연령 조건
        shock = dict(shock_drop=0.03, shock_days=20)
        lrs = replace(base, sigma_target=None, e_max=2.0, **shock)
        vt25 = replace(base, sigma_target=0.25, e_floor=1.0, e_max=2.0, **shock)
        out += [
            ("v3 LRS 2x + 쇼크3%/20일", lrs),
            ("v3 VT σ25% + 쇼크3%/20일", vt25),
            ("v3 VT σ30% EWMA0.94 + 쇼크", replace(vt25, sigma_target=0.30, ewma_lambda=0.94)),
            ("v4 LRS 2x + 쇼크 + 거리3%", replace(lrs, lev_gate_dist=0.03)),
            ("v4 LRS 2x + 쇼크 + 거리5%", replace(lrs, lev_gate_dist=0.05)),
            ("v4 LRS 2x + 쇼크 + 연령40", replace(lrs, lev_min_age=40)),
            ("v4 LRS 2x + 쇼크 + 연령60", replace(lrs, lev_min_age=60)),
            ("v4 LRS 2x + 쇼크 + 연령120", replace(lrs, lev_min_age=120)),
            ("v4 LRS 2x + 쇼크 + 거리3% + 연령60", replace(lrs, lev_gate_dist=0.03, lev_min_age=60)),
            ("v4 LRS 2x + 쇼크 + 12M모멘텀", replace(lrs, mom_filter=252)),
            ("v4 LRS 1.5x + 쇼크", replace(lrs, e_max=1.5)),
            ("v4 LRS 1.5x + 쇼크 + 거리3%", replace(lrs, e_max=1.5, lev_gate_dist=0.03)),
            ("v4 VT σ30% + 쇼크 + 거리3%", replace(vt25, sigma_target=0.30, lev_gate_dist=0.03)),
            ("v4 VT σ30% + 쇼크 + 연령60", replace(vt25, sigma_target=0.30, lev_min_age=60)),
        ]
        if lev_mult >= 3.0:
            out += [
                ("LRS 고정 3x", replace(base, sigma_target=None, e_max=3.0)),
                ("VT σ25% 1~3x", replace(base, sigma_target=0.25, e_floor=1.0, e_max=3.0)),
                ("VT σ30% 1~3x", replace(base, sigma_target=0.30, e_floor=1.0, e_max=3.0)),
            ]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="QQQ:QLD")
    ap.add_argument("--from", dest="d0", default="2000-01-01")
    ap.add_argument("--to", dest="d1", default="2100-01-01")
    ap.add_argument("--is-end", dest="is_end", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--episodes", action="store_true", help="주요 변형의 최대 낙폭 에피소드 5개 출력")
    ap.add_argument("--sensitivity", action="store_true", help="후보 공식(LTM) 파라미터 민감도 표")
    ap.add_argument("--yearly", action="store_true", help="연도별 수익률: B&H · TF-1x · LTM")
    ap.add_argument("--laoer", action="store_true", help="라오어 무한매수 v2.2/v3.0 · VR 을 같은 조건으로 비교 (첫 종목 단독 + LTM 참조)")
    ap.add_argument("--ravg", action="store_true", help="한국 쌍이면 운용 중인 RAVG v2.5 를 참조 행으로 추가")
    a = ap.parse_args()
    code1, _, code2 = a.pair.partition(":")
    code2 = code2 or None
    d0, d1 = date.fromisoformat(a.d0), date.fromisoformat(a.d1)
    with SessionLocal() as s:
        b1 = load(s, code1, d0, d1)
        b2 = load(s, code2, d0, d1) if code2 else None
    bars1, bars2 = align(b1, b2)
    lev_mult = MULT.get(code2 or "", 2.0)
    print(f"\n### {a.pair} · {bars1[0]['date']} ~ {bars1[-1]['date']} · {len(bars1)}봉 · 레버리지 배수 {lev_mult}\n")

    def run_all(start_index: int, tag: str) -> list[dict]:
        res = []
        bh = buy_and_hold(bars1, a.capital, FEES.get(code1, 0.002), start_index)
        bh.label = f"B&H {code1}"
        res.append(fmt(bh))
        if bars2:
            bh2 = buy_and_hold(bars2, a.capital, FEES.get(code2, 0.0095), start_index)
            bh2.label = f"B&H {code2}"
            res.append(fmt(bh2))
        for label, p in variants(lev_mult, bars2 is not None):
            p = replace(p, fee_1x=FEES.get(code1, 0.002), fee_lev=FEES.get(code2 or "", 0.0095))
            if code1 in KR_CODES:
                p = replace(p, commission=KR_COMMISSION, slippage=KR_SLIPPAGE)
            r = run_ltv(bars1, bars2, a.capital, p, start_index=start_index, label=label)
            res.append(fmt(r))
        if a.ravg and bars2 and code1 in KR_CODES:
            # 운용 중인 RAVG v2.5 참조 — 같은 봉, 엔진 기본 비용(KODEX/TIGER 프로필)
            from app.backtests import base_costs_for
            from app.strategy.backtest import run_backtest
            from app.strategy.params import Params
            etf = "TIGER" if code1 == "102110" else "KODEX"
            bt = run_backtest(bars1, bars2, a.capital, Params(**base_costs_for(etf)), start_index=start_index)
            k = bt.kpi
            res.append({"label": "RAVG v2.5 (운용 중)", "total": k["total_return"], "cagr": k["cagr"], "mdd": k["mdd"],
                        "sharpe": k["sharpe"], "calmar": (k["cagr"] / abs(k["mdd"])) if (k["cagr"] is not None and k["mdd"] < 0) else None,
                        "flips": "—", "rebal": k["trades"], "turnover": None, "tim": None, "avg_e": None, "days": len(bt.equity)})
        print(f"**{tag}**\n\n{HEADER}")
        for d in res:
            print(row(d))
        print()
        return res

    if a.laoer:
        from app.strategy.laoer import V22, V30, VRParams, run_infinite, run_vr
        from app.strategy.ltv import drawdown_episodes
        # 비교 대상은 첫 종목(레버리지 ETF 자체) 단독 운용. LTM 은 --pair 의 (1배:레버리지) 쌍으로 같은 창에서 참조.
        target = bars2 if bars2 else bars1          # 예: --pair QQQ:TQQQ → 무한매수/VR 은 TQQQ 에, LTM 은 QQQ+TQQQ
        tcode = code2 or code1
        fee = FEES.get(tcode, 0.0084)
        windows = [("전 구간", None, None), ("2010~2018", "2010-01-01", "2018-12-31"), ("2019~", "2019-01-01", None)]
        for tag, w0, w1 in windows:
            idx = [i for i, b in enumerate(target) if (w0 is None or b["date"] >= w0) and (w1 is None or b["date"] <= w1)]
            if len(idx) < 300:
                continue
            sl = target[idx[0]:idx[-1] + 1]
            res = []
            bh = buy_and_hold(sl, a.capital, fee); bh.label = f"B&H {tcode}"; res.append(fmt(bh))
            from dataclasses import replace as _rp
            res.append(fmt(run_infinite(sl, a.capital, _rp(V22, fee_annual=fee), label="무한매수 v2.2 (40분할·+10%)")))
            res.append(fmt(run_infinite(sl, a.capital, _rp(V30, fee_annual=fee), label="무한매수 v3.0 (20분할·+15%·복리)")))
            res.append(fmt(run_vr(sl, a.capital, VRParams(fee_annual=fee), label="VR G10 · 주식50/풀50 · 밴드15% · 2주")))
            res.append(fmt(run_vr(sl, a.capital, VRParams(g=20.0, fee_annual=fee), label="VR G20 · 50/50")))
            res.append(fmt(run_vr(sl, a.capital, VRParams(init_stock=0.75, fee_annual=fee), label="VR G10 · 75/25")))
            res.append(fmt(run_vr(sl, a.capital, VRParams(band=0.10, fee_annual=fee), label="VR G10 · 밴드10%")))
            if bars2:
                start = next(i for i, b in enumerate(bars1) if b["date"] >= sl[0]["date"])
                ltm_p = replace(LTVParams(lev_multiple=lev_mult), sigma_target=None, e_max=2.0, shock_drop=0.03, shock_days=20,
                                mom_filter=252, fee_1x=FEES.get(code1, 0.002), fee_lev=fee)
                # 창 시작 전 데이터로 지표 워밍업 (창 안 성과만 평가). 첫 창은 워밍업 없이 시작 → 초반 200일은 현금
                r = run_ltv(bars1[: idx[-1] + 1] if False else bars1, bars2, a.capital, ltm_p, start_index=start, label=f"LTM {code1}+{tcode}")
                cut = next(k for k, d in enumerate(r.dates) if d > sl[-1]["date"]) if r.dates[-1] > sl[-1]["date"] else len(r.dates)
                r.equity, r.dates = r.equity[:cut], r.dates[:cut]
                r.kpi = __import__("app.strategy.backtest", fromlist=["compute_kpi"]).compute_kpi(r.equity, r.equity[0], r.trades)
                res.append(fmt(r))
                tf = run_ltv(bars1, bars2, a.capital, replace(ltm_p, e_max=1.0, shock_drop=None, mom_filter=None), start_index=start, label=f"TF-1x {code1}")
                tf.equity, tf.dates = tf.equity[:cut], tf.dates[:cut]
                tf.kpi = __import__("app.strategy.backtest", fromlist=["compute_kpi"]).compute_kpi(tf.equity, tf.equity[0], tf.trades)
                res.append(fmt(tf))
            print(f"**{tag} · {sl[0]['date']} ~ {sl[-1]['date']} ({len(sl)}봉) · 대상 {tcode}**")
            print()
            print(HEADER)
            for d in res:
                print(row(d))
            print()
            if tag == "전 구간":
                for lb, fn in (("무한매수 v2.2", lambda: run_infinite(sl, a.capital, _rp(V22, fee_annual=fee))),
                               ("VR G10 · 50/50", lambda: run_vr(sl, a.capital, VRParams(fee_annual=fee)))):
                    r = fn()
                    print(f"**낙폭 에피소드 — {lb}**")
                    print()
                    print("| 고점 | 저점 | 낙폭 | 회복 | 기간(일) |")
                    print("|---|---|---:|---|---:|")
                    for e in drawdown_episodes(r.dates, r.equity):
                        print(f"| {e['peak']} | {e['trough']} | {e['depth'] * 100:+.1f}% | {e['recovered'] or '미회복'} | {e['days']} |")
                    print()
        return
    out = {"pair": a.pair, "range": [bars1[0]["date"], bars1[-1]["date"]], "full": run_all(0, "전 구간 (워밍업 후)")}
    if a.episodes:
        from app.strategy.ltv import drawdown_episodes
        picks = [lb for lb, _ in variants(lev_mult, bars2 is not None)
                 if lb.startswith("TF-1x") or lb == "v3 LRS 2x + 쇼크3%/20일" or lb.startswith("v4 LRS 2x + 쇼크 + 거리3%") or lb == "v4 LRS 2x + 쇼크 + 연령60"]
        for label, p in variants(lev_mult, bars2 is not None):
            if label not in picks:
                continue
            p = replace(p, fee_1x=FEES.get(code1, 0.002), fee_lev=FEES.get(code2 or "", 0.0095))
            r = run_ltv(bars1, bars2, a.capital, p, label=label)
            print(f"**낙폭 에피소드 — {label}**")
            print()
            print("| 고점 | 저점 | 낙폭 | 회복 | 기간(일) |")
            print("|---|---|---:|---|---:|")
            for e in drawdown_episodes(r.dates, r.equity):
                print(f"| {e['peak']} | {e['trough']} | {e['depth'] * 100:+.1f}% | {e['recovered'] or '미회복'} | {e['days']} |")
            print()
    # ── 후보 공식 LTM (Leveraged Trend-Momentum): MA200 보유 게이트 + 고정 2x + 급락 브레이커 3%/20일 + 12M 모멘텀 확인
    LTM = replace(LTVParams(lev_multiple=lev_mult), sigma_target=None, e_max=2.0, shock_drop=0.03, shock_days=20, mom_filter=252,
                  fee_1x=FEES.get(code1, 0.002), fee_lev=FEES.get(code2 or "", 0.0095))
    if code1 in KR_CODES:
        LTM = replace(LTM, commission=KR_COMMISSION, slippage=KR_SLIPPAGE)
    if a.sensitivity and bars2:
        print("**민감도 — LTM 기준값에서 한 축씩 변형 (전 구간)**")
        print()
        print(HEADER)
        grid = [("기준 LTM", {}),
                ("MA150", {"ma_len": 150}), ("MA250", {"ma_len": 250}),
                ("이탈1%", {"exit_buffer": 0.01}), ("이탈3%", {"exit_buffer": 0.03}),
                ("쇼크2%", {"shock_drop": 0.02}), ("쇼크4%", {"shock_drop": 0.04}),
                ("쇼크 10일", {"shock_days": 10}), ("쇼크 30일", {"shock_days": 30}), ("쇼크 40일", {"shock_days": 40}),
                ("모멘텀 6M", {"mom_filter": 126}), ("모멘텀 9M", {"mom_filter": 189}),
                ("E 1.5", {"e_max": 1.5}), ("E 2.5", {"e_max": 2.5}),
                ("밴드 5%", {"band": 0.05}), ("밴드 20%", {"band": 0.20}),
                ("수수료 2배", {"commission": 0.002, "slippage": 0.001})]
        for lb, kw in grid:
            if kw.get("e_max", 2.0) > lev_mult:
                continue
            r = run_ltv(bars1, bars2, a.capital, replace(LTM, **kw), label=lb)
            print(row(fmt(r)))
        print()
    if a.yearly and bars2:
        def yearly(r):
            out = {}
            prev_v, prev_y = None, None
            first = {}
            for d, v in zip(r.dates, r.equity):
                y = d[:4]
                if y not in first:
                    first[y] = prev_v if prev_v is not None else v
                out[y] = v / first[y] - 1.0
                prev_v = v
            return out
        bh = buy_and_hold(bars1, a.capital, FEES.get(code1, 0.002)); bh.label = "B&H"
        tf = run_ltv(bars1, bars2, a.capital, replace(LTM, e_max=1.0, shock_drop=None, mom_filter=None), label="TF-1x")
        ltm = run_ltv(bars1, bars2, a.capital, LTM, label="LTM")
        lrs = run_ltv(bars1, bars2, a.capital, replace(LTM, shock_drop=None, mom_filter=None), label="LRS 2x")
        ys = [yearly(x) for x in (bh, tf, lrs, ltm)]
        years = sorted(set().union(*[set(y) for y in ys]))
        print("**연도별 수익률 (전 구간)**")
        print()
        print("| 연도 | B&H " + code1 + " | TF-1x | LRS 2x | LTM |")
        print("|---|---:|---:|---:|---:|")
        for y in years:
            cells = [f"{ys[i][y] * 100:+.1f}%" if y in ys[i] else "—" for i in range(4)]
            print(f"| {y} | " + " | ".join(cells) + " |")
        print()
    if a.is_end:
        # OOS: 지표 워밍업은 전체로, 평가는 is_end 이후만 (start_index 절단) — us-transfer-study 와 같은 방식
        idx = next((i for i, b in enumerate(bars1) if b["date"] > a.is_end), None)
        if idx:
            out["is"] = None
            out["oos"] = run_all(idx, f"OOS {a.is_end} 이후 (워밍업은 전체)")
            # IS: 데이터를 is_end 까지만 잘라 실행
            cut1 = [b for b in bars1 if b["date"] <= a.is_end]
            cut2 = [b for b in bars2 if b["date"] <= a.is_end] if bars2 else None
            bars1_full, bars2_full = bars1, bars2
            bars1, bars2 = cut1, cut2  # noqa: F841 — 아래 클로저가 참조
            out["is"] = run_all(0, f"IS ~{a.is_end}")
            bars1, bars2 = bars1_full, bars2_full
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=str)
        print(f"saved {a.json}")


if __name__ == "__main__":
    main()
