"use client";

import type { EvidenceItem } from "@/lib/apiClient";
import { SourceIcon } from "@/components/SourceIcon";
import { SOURCE_META } from "@/lib/format";

// 긍정/주의 근거 목록 — 근거를 소스별 카드로 분리해 가독성을 높인다.
// 각 근거는 소스 칩(아이콘+라벨) → 요약 → 주의 플래그 순으로 위계를 둔다.
export function EvidenceList({
  title,
  tone,
  lead,
  items,
  emptyText,
}: {
  title: string;
  tone: "up" | "down";
  lead?: string | null;
  items: EvidenceItem[];
  emptyText: string;
}) {
  const accent = tone === "up" ? "긍정" : "주의";
  return (
    <section className="glass mt-4 p-5" data-section={`evidence-${tone}`}>
      <div className="flex items-center gap-2">
        <span className={`pill ${tone}`} style={{ padding: "3px 10px", fontSize: 12 }}>
          {accent}
        </span>
        <h2 className="text-[16px] font-bold">{title}</h2>
        <span className="ml-auto text-[12px] text-muted">{items.length}건</span>
      </div>

      {lead && (
        <p className="mt-2.5 rounded-[10px] bg-surface-2 px-3.5 py-2.5 text-[14px] font-semibold leading-relaxed text-navy-soft">
          {lead}
        </p>
      )}

      {items.length === 0 ? (
        <p className="mt-3 text-[13px] text-muted">{emptyText}</p>
      ) : (
        <ul className="mt-3 space-y-2.5">
          {items.map((item, i) => {
            const meta = item.source ? SOURCE_META[item.source] : undefined;
            return (
              <li key={i} className="rounded-[12px] border border-black/5 bg-white/40 p-3.5">
                <div className="flex items-center gap-1.5 text-[12.5px] font-bold text-navy-soft">
                  {meta ? <SourceIcon source={item.source} size={15} /> : <span>•</span>}
                  <span>{meta?.label ?? "종합"}</span>
                </div>
                <p className="mt-1.5 text-[14px] leading-relaxed text-navy-soft">{item.summary}</p>
                {item.risk_flags.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {item.risk_flags.map((flag, j) => (
                      <li
                        key={j}
                        className="flex gap-1.5 rounded-[8px] bg-amber-50 px-2.5 py-1.5 text-[12.5px] leading-relaxed text-amber-700"
                      >
                        <span className="shrink-0">⚠</span>
                        <span>{flag}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
