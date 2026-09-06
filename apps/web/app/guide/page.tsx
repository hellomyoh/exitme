"use client";

/** 가이드 — 매매 공식 개요: 세 공식 비교 + 공식별 비교 블록(같은 위험에서의 수익 · 자산곡선 · 낙폭 · 위기 구간 · 연도별) + 공통 개념 + 용어.
 *  2026-09-06 지시. 같은 날 개편: '보유보다 덜 번다'로 읽히던 비교를 '같은 낙폭이면 얼마나 더 버나'와 '위기 구간에서 얼마나 덜 빠지나'로. */
import Link from "next/link";
import { B, Bullets, GuideShell, Lead, P, Section, Steps, Table } from "../../components/guide";
import { DrawdownChart, GuideChart, YearStrip } from "../../components/guide-chart";
import { COMPARE_ASOF, KR_RAVG, US_LTM, US_TF, type Block } from "./compare-data";

const pct = (v: number, d = 1) => `${(v * 100).toFixed(d)}%`;
const pct0 = (v: number) => `${Math.round(v * 100)}%`;
const days = (d: number | null) => d === null ? "미회복" : d >= 252 ? `${(d / 252).toFixed(1)}년` : `${Math.round(d / 21)}개월`;

/** 공식별 비교 블록 (2026-09-06 개편) — 순서: 헤드라인(같은 위험) → 자산곡선 → 낙폭 그래프 → 위기 구간 표 → 잠긴 시간 → 연도별 → 장점/단점/선택 */
function CompareBlock({ market, title, d, color, capital, note, pros, cons, why }: {
  market: string; title: string; d: Block; color: string; capital: string; note: string;
  pros: string[]; cons: string[]; why: string;
}) {
  const s = d.label.split(" ")[0];
  const re = d.riskEqual;
  const shortB = d.benchLabel.replace(" 단순 보유", "");
  const period = `${d.strategy[0][0].slice(0, 7)} ~ ${COMPARE_ASOF.slice(0, 7)}`;
  const shade: [string, string][] = d.crises.map((c) => [c.from, c.trough]);  // 음영 = 보유가 −20% 넘게 빠진 구간의 하락 국면(고점→저점)
  return (
    <div className="rounded-xl border border-line bg-inset p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-baseline gap-2">
        <span className="rounded-md bg-ink px-2 py-0.5 text-[12px] font-bold text-white">{market}</span>
        <span className="text-[16px] font-bold text-ink">{title}</span>
        <span className="text-[12.5px] text-faint">{period} · {capital}</span>
      </div>

      {/* ① 헤드라인 — 같은 낙폭에서의 수익 */}
      {re && (
        <div className="mb-4 rounded-xl border border-accent/30 bg-accent-dim px-4 py-3 text-[14.5px] leading-relaxed text-ink break-keep">
          같은 최대 낙폭({pct0(d.kpi.mdd)})으로 맞추면 — {shortB} 보유({Math.round(re.weight * 100)}% + 현금) 연 <B>{pct(re.cagr)}</B> vs {s} 연 <B>{pct(d.kpi.cagr)}</B>.
          {d.riskEqualLev && d.levLabel && (<> {d.levLabel.replace(" 단순 보유", "")} 보유({Math.round(d.riskEqualLev.weight * 100)}% + 현금)는 연 <B>{pct(d.riskEqualLev.cagr)}</B>.</>)}
          <span className="block text-[13px] font-normal text-muted">보유가 더 번 것은 더 큰 낙폭을 감수한 대가입니다. 위험을 같게 놓으면 공식이 {pct(d.kpi.cagr - re.cagr)}p 앞섭니다.</span>
        </div>
      )}

      {/* ② 자산곡선 */}
      <GuideChart shade={shade}
        caption={`자산곡선(시작=100, 로그 축) · 붉은 음영 = ${shortB} 보유가 고점 대비 −20% 넘게 빠진 하락 국면(고점→저점). 마우스를 올리면 시점별 배수.`}
        series={[
          { name: d.label.replace(" · TIGER 200 + KODEX 레버리지", ""), points: d.strategy, color },
          { name: d.benchLabel, points: d.bench, color: "var(--color-faint)", dash: "5 4" },
          ...(d.lev && d.levLabel ? [{ name: d.levLabel, points: d.lev, color: "var(--color-up)", dash: "3 4" }] : []),
        ]} />

      {/* ③ 낙폭 그래프 — 핵심 */}
      <div className="mt-5">
        <div className="mb-1 text-[14px] font-bold text-ink">위험 구간에서 얼마나 덜 빠졌나 — 고점 대비 낙폭</div>
        <DrawdownChart shade={shade} floor={-0.5}
          caption="0 은 고점(신고가), 아래로 내려갈수록 고점에서 많이 빠진 상태. 면이 깊고 넓을수록 오래·깊게 잠긴 것. 점은 각 선의 최저점."
          series={[
            { name: s, points: d.ddStrategy, color },
            { name: d.benchLabel, points: d.ddBench, color: "var(--color-faint)", dash: "5 4" },
            ...(d.ddLev && d.levLabel ? [{ name: d.levLabel, points: d.ddLev, color: "var(--color-up)", dash: "3 4" }] : []),
          ]} />
      </div>

      {/* ④ 위기 구간 표 */}
      {d.crises.length > 0 && (
        <div className="mt-4">
          <div className="mb-1 text-[14px] font-bold text-ink">위기 구간별 낙폭과 회복</div>
          <Table head={["구간 (고점 → 저점)", `${shortB} 보유`, s, "보유 회복", `${s} 회복`]}
            rows={d.crises.map((c) => [
              `${c.from} → ${c.trough}`,
              <span key="b" className="text-down">{pct(c.bench)}</span>,
              <span key="s" className={c.strategy > c.bench + 0.05 ? "font-semibold text-ink" : c.strategy < c.bench - 0.02 ? "text-down" : ""}>{pct(c.strategy)}</span>,
              days(c.benchRecoverDays), days(c.strategyRecoverDays),
            ])} />
          <p className="mt-1 text-[12.5px] text-faint">회복 = 고점에서 다시 고점을 넘기까지 걸린 시간. 공식 낙폭은 같은 구간 안에서 공식 자체 고점 대비.</p>
        </div>
      )}

      {/* ⑤ 잠긴 시간 + 지표 */}
      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        <div className="rounded-lg border border-line bg-surface px-3.5 py-2.5 text-[13.5px] leading-relaxed text-muted break-keep">
          <b className="text-ink">고점 대비 −20% 아래에서 보낸 시간</b><br />
          {s} <b className={d.underwater.s20 < d.underwater.b20 ? "text-ink" : "text-down"}>{pct(d.underwater.s20)}</b> · {shortB} 보유 <b className="text-ink">{pct(d.underwater.b20)}</b>
          <span className="block text-[12.5px] text-faint">−10% 아래: {s} {pct0(d.underwater.s10)} · 보유 {pct0(d.underwater.b10)}</span>
        </div>
        <div className="rounded-lg border border-line bg-surface px-3.5 py-2.5 text-[13.5px] leading-relaxed text-muted break-keep">
          <b className="text-ink">낙폭 대비 수익 (연평균 ÷ 최대 낙폭)</b><br />
          {s} <b className="text-ink">{d.kpi.calmar.toFixed(2)}</b> · {shortB} 보유 <b className="text-ink">{d.benchKpi.calmar.toFixed(2)}</b>
          {d.levKpi && d.levLabel && <> · {d.levLabel.replace(" 단순 보유", "")} 보유 <b className="text-ink">{d.levKpi.calmar.toFixed(2)}</b></>}
          <span className="block text-[12.5px] text-faint">연평균 {pct(d.kpi.cagr)} / 최대 낙폭 {pct(d.kpi.mdd)} / {d.kpi.years}년 ×{d.kpi.mult.toFixed(1)} / {d.tradesLabel} · 보유 {pct(d.benchKpi.cagr)} / {pct(d.benchKpi.mdd)} / ×{d.benchKpi.mult.toFixed(1)}</span>
        </div>
      </div>

      {/* ⑥ 연도별 */}
      <div className="mt-4">
        <YearStrip rows={d.yearly} sName={s} bName={`${shortB} 보유`} />
        <p className="mt-1 text-[12.5px] text-faint">굵은 숫자 = 공식이 보유보다 5%p 이상 좋았던 해. 공식이 도운 해와 뒤진 해가 함께 보입니다.</p>
      </div>

      <p className="mt-3 text-[13.5px] leading-relaxed text-muted break-keep">{note}</p>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <div><div className="mb-1 text-[12.5px] font-bold text-up">장점</div><ul className="grid gap-1 text-[13.5px] leading-relaxed text-muted">{pros.map((t, i) => <li key={i} className="break-keep">· {t}</li>)}</ul></div>
        <div><div className="mb-1 text-[12.5px] font-bold text-down">단점</div><ul className="grid gap-1 text-[13.5px] leading-relaxed text-muted">{cons.map((t, i) => <li key={i} className="break-keep">· {t}</li>)}</ul></div>
        <div><div className="mb-1 text-[12.5px] font-bold text-accent">이럴 때 선택</div><p className="text-[13.5px] leading-relaxed text-muted break-keep">{why}</p></div>
      </div>
    </div>
  );
}

export default function GuideIndexPage() {
  return (
    <GuideShell title="매매 공식 가이드" sub="ExitMe 가 쓰는 세 가지 매매 공식을 수식 없이 설명합니다. 어떤 시장에 어떤 공식을 쓰는지부터 시작합니다.">
      <Section title="세 공식 한눈에">
        <Lead>한국은 평균회귀(RAVG), 미국은 추세 추종(TF · LTM). 시장의 성격이 다르기 때문에 공식도 다릅니다.</Lead>
        <Table head={["공식", "시장 · 종목", "한 문장", "거래 빈도", "레버리지"]} rows={[
          [<Link key="r" href="/guide/ravg" className="underline decoration-line underline-offset-4 hover:text-accent">RAVG v2.5</Link>,
            "한국 · KODEX/TIGER 200 + KODEX 레버리지", "떨어질 때 나눠 사고, 한 칸 오르면 익절. 상승장엔 들고 가고 하락장엔 물러난다.", "잦음 (매일 지정가)", "상승장에서만, 노출 100% 초과분"],
          [<Link key="t" href="/guide/tf" className="underline decoration-line underline-offset-4 hover:text-accent">TF</Link>,
            "미국 · QQQ", "200일선 위면 전량 보유, 2% 넘게 뚫리면 전량 현금.", "드묾 (연 1~3회)", "없음"],
          [<Link key="l" href="/guide/ltm" className="underline decoration-line underline-offset-4 hover:text-accent">LTM</Link>,
            "미국 · QQQ + QLD 또는 TQQQ", "TF 처럼 보유하되, 1년 수익이 양수이고 최근 급락이 없을 때만 두 배로.", "적음 (연 10회 안팎)", "조건 충족 시 노출 2배"],
        ]} />
        <P>
          시뮬레이터에서 한국 주식을 고르면 RAVG, 미국 주식을 고르면 LTM(기본) 또는 TF 로 백테스트합니다.
          결과를 실전매매로 전환하면 그 포트의 주문표도 같은 공식으로 계산됩니다. 미국 실전매매는 시작할 때 공식을 직접 고를 수도 있습니다.
        </P>
      </Section>

      <Section title="공식별 비교 — 무엇을 보고 고르나">
        <Lead>수익률만 보면 오르는 시장에서는 그냥 들고 있는 쪽이 대개 이깁니다. 공식은 '같은 위험에서 더 버는가'와 '위기 구간에서 얼마나 덜 빠지는가'로 봅니다.</Lead>
        <Table head={["시장", "공식", "한 줄 장점", "한 줄 단점", "이럴 때 선택"]} rows={[
          ["한국", "RAVG v2.5", "낙폭이 절반, 같은 위험이면 보유의 두 배 수익", "거래가 잦고 규칙이 많다", "코스피처럼 오르내림을 반복하는 시장에서 낙폭을 작게 유지하고 싶을 때"],
          ["미국", "TF", "규칙 두 줄, 느린 하락장(2008·2022)을 통째로 피해 빠져나오기 쉽다", "수익은 보유보다 낮고, 급락·횡보장에서는 도움이 적다", "−50%를 견디기 어렵고 레버리지 없이 안전하게 갈 때"],
          ["미국", "LTM", "확인된 상승 구간에서만 2배로 참여해 수익이 극대화된다", "낙폭은 QQQ 보유와 비슷한 수준 — 2008 제외 시 더 크다", "단순 보유보다 더 벌고 싶고 −40%를 견딜 수 있을 때"],
        ]} />
        <P>
          아래 블록마다 네 가지를 같은 순서로 보여 줍니다. <B>같은 낙폭에서의 수익</B>(보유 비중을 낮춰 최대 낙폭을 공식과 같게 맞춘 뒤 비교),
          <B>자산곡선</B>, <B>낙폭 그래프</B>(고점 대비 몇 % 아래에 있었나), <B>위기 구간 표</B>와 연도별 수익입니다.
          데이터는 {COMPARE_ASOF} 기준 실데이터이며 비용은 단순화 모델입니다. 미국은 두 공식이 모두 판정을 시작한 날부터 같은 구간으로 비교합니다.
        </P>
        <div className="grid gap-5">
          <CompareBlock market="한국 주식" title="RAVG v2.5 vs TIGER 200 단순 보유" d={KR_RAVG} color="var(--color-accent)" capital="자본 1억원 · 레버리지는 KODEX 레버리지"
            note="수익률은 보유와 비슷하지만 낙폭은 절반입니다. 2020년 3월(−38% vs −17%)과 2022년(−36% vs −14%)에 그리드가 멈추고 보유를 줄인 결과이고, −20% 아래에서 보낸 시간은 거의 없습니다. 국내 데이터는 2017년부터 있어 비교 구간이 8년입니다."
            pros={["최대 낙폭이 보유의 절반 — 위기 구간마다 덜 빠졌다", "같은 위험이면 보유의 두 배 수익", "박스권에서도 왕복 익절로 수익"]}
            cons={["1년에 수십 건 거래 — 수수료·세금 영향", "규칙이 많아 주문표가 처음엔 복잡", "쉬지 않고 오르는 해(2025)에는 보유에 뒤진다"]}
            why="코스피처럼 오르내림을 반복하는 시장에서, 수익률보다 낙폭을 작게 유지하는 것이 우선일 때. 매일 지정가를 걸어 두는 운용이 가능할 때." />

          <CompareBlock market="미국 주식" title="TF vs QQQ 단순 보유" d={US_TF} color="var(--color-down)" capital="자본 $1M"
            note="TF 의 효과는 천천히 진행되는 하락장에 집중됩니다. 2008년(−46% vs −4%)과 2022년(−35% vs −22%)에서는 통째로 피했지만, 2020년처럼 한 달 만에 끝난 급락은 −24.5% 로 보유(−29%)와 비슷하게 맞았고, 2015~16년 횡보장에서는 왕복 손실로 보유보다 더 빠졌습니다. 그래서 −10% 아래에서 보낸 시간은 TF 가 보유보다 길고, −20% 아래에서 보낸 시간은 조금 짧습니다. 수익은 보유보다 낮지만 같은 낙폭으로 맞춘 보유보다는 높습니다 — TF 는 느린 하락장에 대한 보험이고, 보험료가 연 4%p 입니다."
            pros={["규칙 두 줄 — 판단은 하루 한 번", "느린 하락장(2008·2022)을 통째로 피해 빠져나오기 쉽다", "같은 낙폭으로 맞춘 보유보다 수익이 높다"]}
            cons={["오르는 장에서는 보유보다 확실히 덜 번다 (5년 구간 대부분 보유가 우위)", "한 달 안에 끝나는 급락(2020)은 피하지 못한다", "횡보장에서 왕복 손실 — 얕은 낙폭 구간에 보유보다 오래 머문다"]}
            why="−50%를 견디기 어려운 돈, 회수 시점이 정해진 돈, 레버리지 없이 미국 지수에 투자하되 2008·2022 같은 장은 피하고 싶을 때." />

          <CompareBlock market="미국 주식" title="LTM vs QQQ 단순 보유 (QLD 단순 보유 참고)" d={US_LTM} color="var(--color-accent)" capital="자본 $1M"
            note="LTM 의 강점은 낙폭이 아니라 수익입니다. 최대 낙폭 −40% 는 QQQ 보유(−46%)와 비슷하고 2008년을 빼면 오히려 큽니다(2015~16년 −40% vs −16%). 대신 같은 낙폭으로 맞춘 QQQ 보유(연 14.7%)나 QLD 보유(연 13.4%)보다 확실히 더 벌고, QLD 를 그냥 들고 있을 때의 −75% 는 피합니다. 레버리지를 쓰기 때문에 −20% 아래에서 보낸 시간이 보유보다 훨씬 길다는 점을 감수해야 합니다."
            pros={["확인된 상승 구간에서만 2배 — 5년 구간의 60% 이상에서 보유를 이긴다", "QLD 를 그냥 들 때의 −75% 를 −40% 로 줄인다", "급락 브레이커·1년 수익 조건이 레버리지를 자동으로 끈다"]}
            cons={["낙폭은 QQQ 보유와 비슷하고 2008 제외 시 더 크다", "−20% 아래에 머무는 시간이 보유의 두 배 이상", "회복 초기에는 1년 수익 조건 때문에 1배로만 참여"]}
            why="단순 보유보다 높은 수익을 원하고 −40%를 견딜 수 있을 때. 레버리지를 켜고 끄는 판단을 규칙에 맡기고 싶을 때. 미국 실전매매의 기본 공식입니다." />
        </div>
      </Section>

      <Section title="세 공식이 공유하는 원칙">
        <Bullets items={[
          { title: "종가로 판단하고, 다음 거래일에 주문한다", desc: "— 장중에 판단을 바꾸지 않습니다. 장 마감 후 그날 종가로 모든 것을 계산해 '내일의 주문표'를 만들고, 다음 날 그대로 실행합니다. 감정이 끼어들 자리를 없애기 위한 구조입니다." },
          { title: "시장 상태를 먼저 본다", desc: "— 세 공식 모두 200일 이동평균선을 기준으로 시장이 오르는 중인지 내리는 중인지 먼저 정하고, 그 다음에 얼마나·무엇을 살지 정합니다. 화면에는 상승장·중립장·하락장으로 표시됩니다." },
          { title: "완충을 둔다", desc: "— 기준선 근처에서 상태가 하루마다 뒤집히지 않도록, 상태가 바뀌려면 기준을 2% 넘게 통과해야 합니다." },
          { title: "잔돈 거래를 하지 않는다", desc: "— 비중이 목표에서 일정 폭 이상 어긋났을 때만 다시 맞춥니다(RAVG 5%p, LTM 10%). 수수료를 아끼고 주문표를 단순하게 유지합니다." },
          { title: "레버리지는 조건부", desc: "— 레버리지 ETF 는 오래 들고 있을수록 불리합니다. 그래서 세 공식 중 어느 것도 레버리지를 '항상' 들지 않습니다. RAVG 는 상승장의 초과 노출만, LTM 은 추세·모멘텀·급락 세 조건이 맞을 때만 씁니다." },
        ]} />
      </Section>

      <Section title="시뮬레이터에서 실전까지">
        <Steps items={[
          { title: "시뮬레이터", desc: "시장·종목·기간·자본을 정해 과거 데이터로 공식을 돌려 봅니다. 수익률뿐 아니라 최대 낙폭과 거래 횟수를 함께 보세요." },
          { title: "실전매매로 전환", desc: "결과 화면의 버튼으로 같은 조건의 실전 포트를 만듭니다. 시뮬레이션 종료 시점의 보유와 현금이 그대로 이어집니다. 미국은 '새 실전매매 시작'에서 공식을 직접 골라 시작할 수도 있습니다." },
          { title: "주문표", desc: "매일 장 마감 후 그 포트의 공식으로 내일 주문을 계산합니다. 한국 포트는 증권사 계좌를 연결해 예약주문으로 넣을 수 있습니다." },
          { title: "기록과 대조", desc: "체결은 자동으로 가져와 기록하고, 계획과 다르게 체결된 것이 있으면 경고로 알려 줍니다(자동 수정은 하지 않습니다)." },
        ]} />
      </Section>

      <Section title="자주 나오는 용어">
        <Table head={["용어", "뜻"]} rows={[
          ["200일선 (MA200)", "최근 200 거래일(약 10개월) 종가의 평균. 장기 추세의 기준선으로 씁니다."],
          ["레짐", "시장 상태. 상승장·중립장·하락장 세 가지."],
          ["노출 (E)", "평가액 대비 시장에 투자된 비율. 100% 는 전액 투자, 200% 는 레버리지로 두 배, 0% 는 전액 현금."],
          ["ATR", "하루 동안 가격이 보통 얼마나 움직이는지 나타내는 변동성 지표. RAVG 의 그리드 간격을 정합니다."],
          ["그리드", "전일 종가 아래 일정 간격으로 깔아 두는 매수 지정가 묶음."],
          ["익절", "산 가격보다 정해진 만큼 오르면 파는 것. RAVG 중립장의 기본 수익원."],
          ["지정가 / 시장가", "지정가는 정한 가격에 닿아야 체결, 시장가는 그 시점 가격에 바로 체결. RAVG 는 지정가, TF·LTM 은 다음 날 시가 시장가를 씁니다."],
          ["밴드", "비중을 다시 맞추는 기준 폭. 이 폭 안의 어긋남은 그냥 둡니다."],
          ["최대 낙폭 (MDD)", "고점에서 저점까지 가장 크게 빠진 비율. 공식을 고를 때 수익률보다 먼저 볼 숫자입니다."],
          ["낙폭 그래프", "매일 '고점 대비 몇 % 아래인가'를 그린 것. 0 이면 신고가, 깊고 넓은 면은 오래·깊게 잠긴 구간."],
          ["같은 낙폭에서의 수익", "보유 비중을 낮춰 최대 낙폭을 공식과 같게 맞췄을 때의 수익. 위험을 같게 놓고 비교하는 방법."],
        ]} />
        <P>
          더 자세한 규칙은 각 공식 페이지에 있습니다. <B>RAVG</B> → <B>TF</B> → <B>LTM</B> 순서로 읽으면 뒤 공식이 앞 공식 위에 어떻게 얹히는지 보입니다.
        </P>
      </Section>
    </GuideShell>
  );
}
