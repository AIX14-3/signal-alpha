const LEVEL: Record<string, { label: string; cls: string }> = {
  HIGH: { label: "高", cls: "text-red bg-red/10" },
  MID: { label: "中", cls: "text-[#B45309] bg-[#F59E0B]/12" },
  LOW: { label: "低", cls: "text-muted bg-surface-2" },
};

export type RiskItem = { level: string; title: string; detail?: string | null };

export function RiskList({ risks }: { risks: RiskItem[] }) {
  if (risks.length === 0) {
    return <p className="text-[14px] text-muted">표시할 리스크 항목이 없습니다.</p>;
  }
  return (
    <div>
      {risks.map((risk, index) => {
        const meta = LEVEL[(risk.level ?? "LOW").toUpperCase()] ?? LEVEL.LOW;
        return (
          <div
            key={`${risk.title}-${index}`}
            className="flex gap-3 border-b border-dashed border-line py-3 last:border-0"
          >
            <span className={`shrink-0 rounded-[7px] px-2 py-1 text-[11px] font-extrabold ${meta.cls}`}>
              {meta.label}
            </span>
            <div>
              <div className="text-[14px] font-semibold">{risk.title}</div>
              {risk.detail && <div className="mt-0.5 text-[12.5px] text-navy-soft">{risk.detail}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
