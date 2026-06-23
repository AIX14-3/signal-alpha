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

/** 0–100 또는 0–10 score → /10 표기값 + dots(5점) 채움 개수. */
function toTile(detail: FactorDetail): { num: string; filled: number } {
  const value = detail?.score;
  if (value === null || value === undefined) return { num: "–", filled: 0 };
  const onTen = value > 10 ? value / 10 : value;
  return { num: onTen.toFixed(1), filled: Math.max(0, Math.min(5, Math.round(onTen / 2))) };
}

/** 시안 v13 6타일: 5개 팩터(chip + 점수 + dots) + 핵심 지표 타일. */
export function FactorGrid({
  scoreBreakdown,
  metrics,
}: {
  scoreBreakdown: Record<string, FactorDetail>;
  metrics: { label: string; value: string }[];
}) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {FACTOR_MAP.map((factor) => {
        const detail = scoreBreakdown?.[factor.source];
        const c = chip(detail?.direction);
        const { num, filled } = toTile(detail);
        return (
          <div key={factor.label} className="card p-5">
            <div className="flex items-center justify-between">
              <span className="text-[13px] font-bold">{factor.label}</span>
              <span className={`pill px-[11px] py-1 text-[11px] font-extrabold ${c.cls}`}>
                {c.label}
              </span>
            </div>
            <div className="mt-3 text-[34px] font-extrabold">{num}</div>
            <div className="text-[11.5px] text-muted">{factor.hint}</div>
            <div className="mt-3 flex gap-[5px]">
              {Array.from({ length: 5 }).map((_, i) => (
                <span
                  key={i}
                  className={`h-[13px] w-[13px] rounded-full ${
                    i < filled ? "bg-sky" : "bg-surface-2 shadow-[inset_1px_1px_3px_rgba(15,27,51,0.12)]"
                  }`}
                />
              ))}
            </div>
          </div>
        );
      })}

      {/* 핵심 지표 타일 */}
      <div className="card p-5">
        <span className="text-[13px] font-bold">핵심 지표</span>
        <div className="mt-3 flex flex-col gap-[7px] text-[13px] text-navy-soft">
          {metrics.length === 0 ? (
            <span className="text-muted">지표 준비 중</span>
          ) : (
            metrics.map((m) => (
              <div key={m.label} className="flex justify-between">
                <span>{m.label}</span>
                <b>{m.value}</b>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
