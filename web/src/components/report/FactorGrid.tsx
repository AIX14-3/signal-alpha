import { FACTOR_MAP } from "@/lib/format";

type FactorDetail = { score?: number | null; direction?: string | null } | null | undefined;

const DIR_CHIP: Record<string, { label: string; cls: string }> = {
  POSITIVE: { label: "↗ 상승", cls: "bg-green text-white" },
  NEGATIVE: { label: "↘ 둔화", cls: "bg-red text-white" },
  NEUTRAL: { label: "→ 횡보", cls: "bg-muted text-white" },
};

function chip(direction?: string | null) {
  return DIR_CHIP[(direction ?? "NEUTRAL").toUpperCase()] ?? DIR_CHIP.NEUTRAL;
}

function tileScore(detail: FactorDetail): string {
  const value = detail?.score;
  if (value === null || value === undefined) return "–";
  // 0–100 스케일이면 /10, 이미 0–10이면 그대로.
  return value > 10 ? (value / 10).toFixed(1) : Number(value).toFixed(1);
}

/** 시안 6타일 팩터 그리드 (FACTOR_MAP 기준, score_breakdown에서 소스별 값 추출). */
export function FactorGrid({
  scoreBreakdown,
}: {
  scoreBreakdown: Record<string, FactorDetail>;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {FACTOR_MAP.map((factor) => {
        const detail = scoreBreakdown?.[factor.source];
        const c = chip(detail?.direction);
        return (
          <div key={factor.label} className="card p-5">
            <div className="flex items-center justify-between">
              <span className="text-[13px] font-bold">{factor.label}</span>
              <span className={`pill px-[11px] py-1 text-[11px] font-extrabold ${c.cls}`}>
                {c.label}
              </span>
            </div>
            <div className="mt-3 text-[34px] font-extrabold">{tileScore(detail)}</div>
            <div className="text-[11.5px] text-muted">{factor.hint}</div>
          </div>
        );
      })}
    </div>
  );
}
