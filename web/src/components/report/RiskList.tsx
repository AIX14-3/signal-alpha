const LEVEL: Record<string, { text: string; cls: string }> = {
  HIGH: { text: "HIGH", cls: "font-extrabold text-red" },
  MID: { text: "MID", cls: "font-semibold text-[#B45309]" },
  LOW: { text: "LOW", cls: "text-muted" },
};

export type RiskItem = { level: string; title: string; detail?: string | null };

/** 시안 v13 prose 리스크: 항목명 좌 · 등급(HIGH/MID/LOW) 우. */
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
            className="flex items-center justify-between border-b border-dashed border-line py-[9px] text-[13.5px] last:border-0"
            title={risk.detail ?? undefined}
          >
            <b className="font-semibold">{risk.title}</b>
            <span className={`text-[12px] ${meta.cls}`}>{meta.text}</span>
          </div>
        );
      })}
    </div>
  );
}
