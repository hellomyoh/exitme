"use client";

/** 가이드 — 매매 공식 개요: 세 공식 비교 + 공통 개념 + 용어 (2026-09-06 지시). */
import Link from "next/link";
import { B, Bullets, GuideShell, Lead, P, Section, Steps, Table } from "../../components/guide";
import { GuideChart } from "../../components/guide-chart";
import { COMPARE_ASOF, KR_RAVG, US_LTM, US_TF, type Kpi } from "./compare-data";

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
const KpiTable = ({ rows }: { rows: [string, Kpi, string][] }) => (
  <Table head={["", "연평균 수익률", "최대 낙폭", "배수", "거래"]}
    rows={rows.map(([name, k, trades]) => [name, pct(k.cagr), pct(k.mdd), `×${k.mult.toFixed(1)}`, trades])} />
);

/** 공식별 비교 블록 — 그래프(전략 vs 단순 보유) + 요약 지표 + 장점·단점·선택 이유 (2026-09-06 지시) */
function CompareBlock({ market, title, chart, kpis, caption, pros, cons, why }: {
  market: string; title: string; chart: React.ReactNode; kpis: [string, Kpi, string][]; caption: string;
  pros: string[]; cons: string[]; why: string;
}) {
  return (
    <div className="rounded-xl border border-line bg-inset p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-baseline gap-2">
        <span className="rounded-md bg-ink px-2 py-0.5 text-[12px] font-bold text-white">{market}</span>
        <span className="text-[16px] font-bold text-ink">{title}</span>
      </div>
      {chart}
      <div className="mt-4"><KpiTable rows={kpis} /></div>
      <p className="mt-2 text-[13px] leading-relaxed text-faint break-keep">{caption}</p>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <div><div className="mb-1 text-[12.5px] font-bold text-up">장점</div><ul className="grid gap-1 text-[13.5px] leading-relaxed text-muted">{pros.map((s, i) => <li key={i} className="break-keep">· {s}</li>)}</ul></div>
        <div><div className="mb-1 text-[12.5px] font-bold text-down">단점</div><ul className="grid gap-1 text-[13.5px] leading-relaxed text-muted">{cons.map((s, i) => <li key={i} className="break-keep">· {s}</li>)}</ul></div>
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
          결과를 실전매매로 전환하면 그 포트의 주문표도 같은 공식으로 계산됩니다.
        </P>
      </Section>

      <Section title="공식별 비교 — 단순 보유와 나란히">
        <P>
          같은 종목을 <B>그냥 들고 있었을 때</B>와 <B>공식을 적용했을 때</B>의 자산곡선입니다. 시작을 100으로 맞추고 로그 축으로 그려, 하락장에서 얼마나 덜 빠졌는지와
          끝에서 몇 배가 되었는지를 함께 볼 수 있습니다. 선 위에 마우스를 올리면 시점별 배수가 나옵니다. 데이터는 {COMPARE_ASOF} 기준 실데이터이며 비용은 단순화 모델입니다.
        </P>
        <Table head={["시장", "공식", "한 줄 장점", "한 줄 단점", "이럴 때 선택"]} rows={[
          ["한국", "RAVG v2.5", "낙폭이 절반 이하, 박스권에서도 익절로 수익", "거래가 잦고 규칙이 많다", "코스피처럼 오르내림을 반복하는 시장에서 낙폭을 작게 유지하고 싶을 때"],
          ["미국", "TF", "규칙 두 줄, 큰 하락을 통째로 피해 빠져나오기 쉽다", "상승의 일부를 놓쳐 단순 보유보다 덜 번다", "−50%를 견디기 어렵고 레버리지 없이 안전하게 갈 때"],
          ["미국", "LTM", "확인된 상승 구간에서만 2배로 참여해 수익이 극대화된다", "레버리지 구간에서 −40% 안팎 낙폭", "단순 보유보다 더 벌고 싶고 −40%를 견딜 수 있을 때"],
        ]} />
        <div className="grid gap-4">
          <CompareBlock market="한국 주식" title="RAVG v2.5 vs TIGER 200 단순 보유"
            chart={<GuideChart caption={`${KR_RAVG.strategy[0][0].slice(0, 7)} ~ ${COMPARE_ASOF.slice(0, 7)} · 자본 1억원 · 레버리지는 KODEX 레버리지`}
              series={[
                { name: "RAVG v2.5", points: KR_RAVG.strategy, color: "var(--color-accent)" },
                { name: "TIGER 200 단순 보유", points: KR_RAVG.bench, color: "var(--color-faint)", dash: "5 4" },
              ]} />}
            kpis={[["RAVG v2.5", KR_RAVG.kpi, `${KR_RAVG.trades}회 왕복`], ["TIGER 200 단순 보유", KR_RAVG.benchKpi, "—"]]}
            caption="수익률은 단순 보유와 비슷하지만 최대 낙폭이 절반 아래입니다. 2020년 3월과 2022년 하락장에서 그리드가 멈추고 보유를 줄인 결과입니다. 국내 데이터는 2017년부터 있어 비교 구간이 8년입니다."
            pros={["최대 낙폭이 단순 보유의 절반 이하", "박스권에서도 왕복 익절로 수익", "레버리지는 상승장에서만 조건부"]}
            cons={["1년에 수십 건 거래 — 수수료·세금 영향", "규칙이 많아 주문표가 처음엔 복잡", "쉬지 않고 오르는 장에서는 보유에 뒤진다"]}
            why="코스피처럼 오르내림을 반복하는 시장에서, 수익률보다 낙폭을 작게 유지하는 것이 우선일 때. 매일 지정가를 걸어 두는 운용이 가능할 때." />

          <CompareBlock market="미국 주식" title="TF vs QQQ 단순 보유"
            chart={<GuideChart caption={`${US_TF.strategy[0][0].slice(0, 7)} ~ ${COMPARE_ASOF.slice(0, 7)} · 자본 $1M · 두 미국 공식이 모두 판정을 시작한 날부터 같은 구간으로 비교`}
              series={[
                { name: "TF · QQQ", points: US_TF.strategy, color: "var(--color-down)" },
                { name: "QQQ 단순 보유", points: US_TF.bench, color: "var(--color-faint)", dash: "5 4" },
              ]} />}
            kpis={[["TF · QQQ", US_TF.kpi, `${US_TF.trades}회 왕복`], ["QQQ 단순 보유", US_TF.benchKpi, "—"]]}
            caption="배수는 단순 보유의 절반이지만 최대 낙폭도 절반입니다. 2008년·2022년 하락장에서 곡선이 평평한 구간이 현금으로 빠져나온 기간입니다. 200일선 근처의 작은 톱니가 왕복 손실입니다."
            pros={["규칙 두 줄 — 판단은 하루 한 번", "큰 하락을 통째로 피해 빠져나오기 쉽다", "회복이 쉬워 끝까지 들고 갈 수 있다"]}
            cons={["상승 초반을 놓쳐 단순 보유보다 덜 번다", "200일선 근처 횡보에서 사고팔기 반복", "하루 이틀 급락은 피하지 못한다"]}
            why="−50%를 견디기 어려운 돈, 회수 시점이 정해진 돈, 레버리지 없이 미국 지수에 투자하되 최악은 피하고 싶을 때." />

          <CompareBlock market="미국 주식" title="LTM vs QQQ 단순 보유 (QLD 단순 보유 참고)"
            chart={<GuideChart caption={`${US_LTM.strategy[0][0].slice(0, 7)} ~ ${COMPARE_ASOF.slice(0, 7)} · 자본 $1M · 점선 회색 = QQQ 보유, 점선 붉은색 = QLD(2배) 보유`}
              series={[
                { name: "LTM · QQQ+QLD", points: US_LTM.strategy, color: "var(--color-accent)" },
                { name: "QQQ 단순 보유", points: US_LTM.bench, color: "var(--color-faint)", dash: "5 4" },
                { name: "QLD 단순 보유", points: US_LTM.lev, color: "var(--color-up)", dash: "3 4" },
              ]} />}
            kpis={[["LTM · QQQ+QLD", US_LTM.kpi, `체결 ${US_LTM.fills}회`], ["QQQ 단순 보유", US_LTM.benchKpi, "—"], ["QLD 단순 보유", US_LTM.levKpi, "—"]]}
            caption="QQQ 보유보다 더 벌면서 낙폭은 QQQ 보유보다 작습니다. QLD를 그냥 들고 있으면 배수는 더 크지만 −75%를 지나야 합니다. LTM은 그 낙폭을 −40%로 줄이는 대신 QLD 보유 수익의 일부를 포기합니다."
            pros={["확인된 상승 구간에서만 2배 — 수익 극대화", "하락장 회피는 TF와 동일", "급락 브레이커·1년 수익 조건이 레버리지를 자동으로 끈다"]}
            cons={["레버리지 구간에서 −40% 안팎 낙폭", "회복 초기에는 1년 수익 조건 때문에 1배로만 참여", "레버리지 ETF 보수가 높다"]}
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
          { title: "실전매매로 전환", desc: "결과 화면의 버튼으로 같은 조건의 실전 포트를 만듭니다. 시뮬레이션 종료 시점의 보유와 현금이 그대로 이어집니다." },
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
        ]} />
        <P>
          더 자세한 규칙은 각 공식 페이지에 있습니다. <B>RAVG</B> → <B>TF</B> → <B>LTM</B> 순서로 읽으면 뒤 공식이 앞 공식 위에 어떻게 얹히는지 보입니다.
        </P>
      </Section>
    </GuideShell>
  );
}
