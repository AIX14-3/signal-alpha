import Link from "next/link";
import { PosthocAlignmentSummary } from "@/components/PosthocAlignmentSummary";

const METHOD_STEPS = [
  {
    title: "1. 발행 시점 고정",
    body: "저널 기준은 사용자가 저널에 저장한 발행 당시 데이터 방향성, 소스 간 일치도, 근거를 고정해 이후 결과와 분리합니다.",
  },
  {
    title: "2. 이후 관측 결과 분리",
    body: "7거래일과 30거래일이 지난 뒤 확정된 관측 결과만 비교합니다. 아직 결과가 충분하지 않은 케이스는 확정 대기로 분리합니다.",
  },
  {
    title: "3. 기준별 사후정합성 비교",
    body: "저널 기준은 사용자가 저장한 케이스를 보고, 전체 발행 신호 기준은 별도 검증 기록의 5거래일 확정 결과를 집계합니다.",
  },
  {
    title: "4. 방향성별 breakdown 공개",
    body: "긍정 방향성과 부정 방향성을 나눠 확정, 정합, 비정합, 대기 건수를 표시합니다. 전체 비율 하나로 표본 차이를 숨기지 않습니다.",
  },
  {
    title: "5. 한계와 반영 시점 함께 공개",
    body: "표본 부족, 확정 대기, 마지막 검증 반영 시점을 함께 표시합니다. 사후정합성은 미래 결과를 보장하지 않습니다.",
  },
];

const TERMS = [
  ["사후정합성", "발행 당시 데이터 방향성이 이후 관측 결과와 얼마나 일관되게 이어졌는지 확인하는 검증 지표입니다."],
  ["저널 기준", "사용자가 저널에 저장한 당시 판단 기록을 기준으로 계산하는 사후정합성입니다."],
  ["전체 발행 신호 기준", "서비스가 발행한 전체 신호 중 별도 검증 기록이 남은 케이스를 기준으로 계산합니다."],
  ["방향성별 breakdown", "긍정 방향성과 부정 방향성을 분리해 확정, 정합, 비정합, 대기 건수를 보여주는 보조 지표입니다."],
  ["마지막 검증 반영", "확정 결과가 화면에 반영된 가장 최근 시점을 뜻합니다."],
  ["확정 대기", "아직 비교 가능한 거래일이 충분히 지나지 않아 결과 비교에서 분리된 상태입니다."],
  ["표본 부족", "확정된 케이스가 충분하지 않아 비율을 강하게 해석하면 안 되는 상태입니다."],
  ["사용자 판단 보조", "서비스가 제공하는 정보는 추가 확인을 돕는 근거이며 특정 종목 행동 권유가 아닙니다."],
];

export default function MethodologyPage() {
  return (
    <div className="py-12" data-page="methodology">
      <section className="max-w-[760px]">
        <div className="text-[13px] font-bold uppercase tracking-[0.12em] text-sky-deep">
          Methodology
        </div>
        <h1 className="mt-3 text-[34px] font-extrabold leading-tight">
          데이터 방향성은 어떻게 검증되나요?
        </h1>
        <p className="mt-4 text-[16px] leading-7 text-navy-soft">
          사후정합성은 발행 당시의 데이터 방향성, 소스 간 일치도, 근거를 이후 관측 결과와
          분리해 비교하는 검증 기록입니다. 특정 종목 행동을 권유하지 않으며, 사용자 판단 보조를
          위한 근거와 한계를 함께 보여줍니다.
        </p>
      </section>

      <PosthocAlignmentSummary />

      <section className="mt-8 grid grid-cols-1 gap-5 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="card p-6">
          <h2 className="text-[20px] font-bold">계산 방식</h2>
          <div className="mt-5 space-y-4">
            {METHOD_STEPS.map((step) => (
              <div key={step.title} className="border-l-2 border-sky/40 pl-4">
                <h3 className="text-[15px] font-bold">{step.title}</h3>
                <p className="mt-1 text-[13.5px] leading-6 text-navy-soft">{step.body}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="card p-6">
          <h2 className="text-[20px] font-bold">해석 기준</h2>
          <dl className="mt-5 space-y-4">
            {TERMS.map(([term, description]) => (
              <div key={term}>
                <dt className="text-[14px] font-bold text-navy">{term}</dt>
                <dd className="mt-1 text-[13.5px] leading-6 text-navy-soft">{description}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section className="card mt-8 p-6">
        <h2 className="text-[20px] font-bold">무엇을 하지 않나요?</h2>
        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
          {[
            "미래 결과를 보장하지 않습니다.",
            "특정 종목 행동을 권유하지 않습니다.",
            "개별 사용자의 상황을 대신 판단하지 않습니다.",
          ].map((text) => (
            <div key={text} className="rounded-[8px] bg-surface-2 px-4 py-3 text-[13.5px] font-semibold text-navy-soft">
              {text}
            </div>
          ))}
        </div>
        <p className="mt-4 text-[13px] leading-6 text-muted">
          사후정합성은 과거 데이터 방향성의 사후 검증 지표입니다. 데이터 지연, 표본 부족,
          확정 대기, 시장 외부 요인에 따라 해석에는 추가 확인이 필요합니다.
        </p>
      </section>

      <section className="mt-8 flex flex-wrap items-center justify-between gap-3 rounded-[12px] bg-surface-2 p-5">
        <div>
          <h2 className="text-[17px] font-bold">근거를 함께 확인하세요</h2>
          <p className="mt-1 text-[13.5px] text-muted">
            리포트에서는 종목별 데이터 방향성, 소스 간 일치도, 근거 상세를 함께 볼 수 있습니다.
          </p>
        </div>
        <Link href="/" className="brand-grad rounded-full px-5 py-2.5 text-[14px] font-bold text-white">
          종목 검색
        </Link>
      </section>
    </div>
  );
}
