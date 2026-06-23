import type { ReactNode } from "react";
import { directionLabel, scoreOutOf10 } from "@/lib/format";
import { FactorGrid } from "@/components/report/FactorGrid";
import { RiskList, type RiskItem } from "@/components/report/RiskList";

export type ReportData = {
  stockCode: string;
  stockName: string;
  market?: string | null;
  sector?: string | null;
  finalScore: number | null;
  direction: string | null;
  summary: string | null;
  thesis: string | null;
  sourceAgreement: string | null;
  scoreBreakdown: Record<string, { score?: number | null; direction?: string | null } | null>;
  metrics: { label: string; value: string }[];
  tags: string[];
  risks: RiskItem[];
  notice?: string;
};

const CALL: Record<string, { label: string; cls: string }> = {
  up: { label: "BUY · 매수 우위", cls: "brand-grad" },
  down: { label: "SELL · 매도 우위", cls: "bg-red" },
  flat: { label: "HOLD · 중립", cls: "bg-muted" },
};

export function ReportView({ data, actions }: { data: ReportData; actions?: ReactNode }) {
  const verdict = directionLabel(data.direction);
  const call = CALL[verdict.tone];
  const meta = [data.stockCode, data.market, data.sector].filter(Boolean).join(" · ");

  return (
    <article className="py-8">
      {/* mast */}
      <div className="card mb-[18px] flex flex-wrap items-center justify-between gap-4 px-7 py-6">
        <div>
          <h1 className="text-[34px] font-extrabold tracking-[-0.02em]">{data.stockName}</h1>
          <div className="mt-1.5 text-[12.5px] text-muted">{meta}</div>
        </div>
        {actions}
      </div>

      {/* hero: 블롭 AI Score + BUY pill + thesis */}
      <div
        className="mb-[18px] flex flex-wrap items-center gap-7 rounded-[18px] border border-line p-[30px]"
        style={{ background: "linear-gradient(135deg,#EFF8FE,#EFFBF4)" }}
      >
        <div
          className="grid h-[150px] w-[150px] flex-none place-items-center border border-line bg-surface shadow-[var(--shadow-card)]"
          style={{ borderRadius: "42% 58% 56% 44% / 52% 44% 56% 48%" }}
        >
          <div className="text-center">
            <b className="text-[48px] font-extrabold leading-none tracking-[-0.03em]">
              {scoreOutOf10(data.finalScore)}
            </b>
            <span className="mt-1 block text-[11px] text-muted">/ 10 AI Score</span>
          </div>
        </div>
        <div>
          <div className={`pill mb-3 px-5 py-2.5 text-[16px] font-extrabold text-white ${call.cls}`}>
            ● {call.label}
          </div>
          <p className="max-w-[42ch] text-[14.5px] leading-[1.6] text-navy-soft">
            {data.thesis ?? data.summary ?? "분석 근거를 준비 중입니다."}
          </p>
        </div>
      </div>

      {/* 6타일 팩터 그리드 */}
      <div className="mb-[18px]">
        <FactorGrid scoreBreakdown={data.scoreBreakdown} metrics={data.metrics} />
      </div>

      {/* prose: 핵심 요약 + 리스크 */}
      <div className="grid gap-4 md:grid-cols-[1.5fr_1fr]">
        <div className="card p-7">
          <div className="text-[11px] font-bold uppercase tracking-[0.12em] text-muted">
            01 — 핵심 요약
          </div>
          <p className="mt-3 text-[14px] leading-[1.7] text-navy-soft">
            {data.summary ?? "요약 정보가 아직 없습니다."}
          </p>
          {data.tags.length > 0 && (
            <div className="mt-3.5 flex flex-wrap gap-2">
              {data.tags.map((tag) => (
                <span
                  key={tag}
                  className="rounded-full bg-sky/10 px-[13px] py-1.5 text-[11.5px] font-bold text-sky-deep"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="card p-7">
          <div className="text-[11px] font-bold uppercase tracking-[0.12em] text-muted">
            02 — 리스크
          </div>
          <div className="mt-2">
            <RiskList risks={data.risks} />
          </div>
        </div>
      </div>

      <p className="mx-auto mt-6 max-w-[60ch] text-center text-[12px] leading-[1.6] text-muted">
        {data.notice ??
          "본 리포트는 AI 멀티에이전트가 공시·재무·뉴스·수급·시계열 데이터를 분석한 참고 자료이며, 투자 권유나 수익을 보장하지 않습니다."}
      </p>
    </article>
  );
}
