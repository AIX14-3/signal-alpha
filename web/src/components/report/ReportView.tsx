import { directionLabel } from "@/lib/format";
import { FactorGrid } from "@/components/report/FactorGrid";
import { RiskList, type RiskItem } from "@/components/report/RiskList";
import { ScoreGauge } from "@/components/report/ScoreGauge";

export type ReportData = {
  stockCode: string;
  stockName: string;
  market?: string | null;
  meta?: string | null;
  finalScore: number | null;
  direction: string | null;
  summary: string | null;
  sourceAgreement: string | null;
  scoreBreakdown: Record<string, { score?: number | null; direction?: string | null } | null>;
  risks: RiskItem[];
  notice?: string;
};

const TONE: Record<string, string> = { up: "text-green", down: "text-red", flat: "text-muted" };

export function ReportView({ data }: { data: ReportData }) {
  const verdict = directionLabel(data.direction);

  return (
    <article className="py-10">
      {/* mast */}
      <div className="card mb-[18px] flex flex-wrap items-center justify-between gap-4 px-7 py-6">
        <div>
          <h1 className="text-[34px] font-extrabold tracking-[-0.02em]">{data.stockName}</h1>
          <div className="mt-1.5 text-[12.5px] text-muted">
            {data.stockCode}
            {data.market ? ` · ${data.market}` : ""}
            {data.meta ? ` · ${data.meta}` : ""}
          </div>
        </div>
        <div className={`text-[15px] font-bold ${TONE[verdict.tone]}`}>{verdict.label}</div>
      </div>

      {/* 점수 + 핵심요약 */}
      <div className="mb-[18px] grid gap-5 lg:grid-cols-[260px_1fr]">
        <ScoreGauge
          score={data.finalScore}
          direction={data.direction}
          agreement={data.sourceAgreement}
        />
        <div className="card p-7">
          <div className="text-[11px] font-bold uppercase tracking-[0.12em] text-muted">
            핵심 요약
          </div>
          <p className="mt-3 text-[14.5px] leading-[1.7] text-navy-soft">
            {data.summary ?? "요약 정보가 아직 없습니다."}
          </p>
        </div>
      </div>

      {/* 6타일 팩터 그리드 */}
      <div className="mb-[18px]">
        <FactorGrid scoreBreakdown={data.scoreBreakdown} />
      </div>

      {/* 리스크 */}
      <div className="card p-7">
        <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.12em] text-muted">
          리스크
        </div>
        <RiskList risks={data.risks} />
      </div>

      <p className="mx-auto mt-6 max-w-[60ch] text-center text-[12px] leading-[1.6] text-muted">
        {data.notice ??
          "본 리포트는 AI 멀티에이전트가 공시·재무·뉴스·수급·시계열 데이터를 분석한 참고 자료이며, 투자 권유나 수익을 보장하지 않습니다."}
      </p>
    </article>
  );
}
