"use client";

import { Search } from "lucide-react";
import type { ReportSource, SourceKey } from "@/lib/apiClient";
import { SourceIcon } from "@/components/SourceIcon";
import { directionLabel, SOURCE_META } from "@/lib/format";

// S5 수요·확장 흔적 카드 — 채용·검색·특허 등 대체 데이터에서 관측된 수요/확장의 "흔적"을
// 예측이 아닌 관측 표시로 보여준다(ML 없음). 소스별 흔적 유형 배지 + 방향 + 요약.
const TRACE: { key: SourceKey; badge: string; hint: string }[] = [
  { key: "hiring", badge: "인력 확장 흔적", hint: "채용 동향으로 본 사업 확장 신호" },
  { key: "datalab", badge: "수요 관심 흔적", hint: "검색 관심도로 본 수요 변화" },
  { key: "patent", badge: "기술 확장 흔적", hint: "특허 출원으로 본 기술 투자" },
];

export function TraceCards({ sources }: { sources: ReportSource[] }) {
  const byKey = new Map(sources.map((s) => [s.source, s] as const));
  const cards = TRACE.map((t) => ({ ...t, src: byKey.get(t.key) })).filter(
    (t) => t.src,
  );
  if (cards.length === 0) return null;

  return (
    <section className="glass relative mt-12 p-5" data-section="trace-cards">
      <span className="file-tab">
        <Search size={13} /> 수요·확장 흔적
      </span>
      <p className="text-[12.5px] text-muted">
        채용·검색·특허 등 대체 데이터에서 감지된 수요/확장의 흔적입니다. 예측이 아니라 관측된
        변화예요.
      </p>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
        {cards.map((t) => {
          const meta = SOURCE_META[t.key];
          const dir = directionLabel(t.src!.direction);
          return (
            <div key={t.key} className="hover-lift rounded-[14px] border border-white/60 bg-white/50 p-4">
              <div className="flex items-center gap-2">
                <SourceIcon source={t.key} size={18} className="text-navy-soft" />
                <span className="text-[13px] font-bold">{meta.label}</span>
                <span
                  className={`pill ${dir.tone} ml-auto`}
                  style={{ padding: "2px 8px", fontSize: 11 }}
                >
                  {dir.label}
                </span>
              </div>
              <div className="mt-2 inline-block rounded-full bg-surface-2 px-2.5 py-1 text-[11.5px] font-semibold text-navy-soft">
                {t.badge}
              </div>
              <p className="mt-2 text-[13px] leading-relaxed text-navy-soft">
                {t.src!.summary ?? t.hint}
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
}
