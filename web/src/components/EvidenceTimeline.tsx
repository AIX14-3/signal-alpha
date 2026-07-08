"use client";

import { Clock } from "lucide-react";
import { useEffect, useState } from "react";
import { getReportTimeline, type SourceDetailItem } from "@/lib/apiClient";
import { SourceIcon } from "@/components/SourceIcon";
import { directionLabel, safeHttpUrl, SOURCE_META } from "@/lib/format";

// S2 Evidence Timeline — 여러 소스의 근거를 한 종목에서 시간순(event_date)으로 모아 보여준다.
// 소스 상세가 "한 소스"만 보여주는 것과 달리, 소스를 가로질러 "언제 무슨 근거가 있었나"를 훑는다.
export function EvidenceTimeline({ stockCode }: { stockCode: string }) {
  const [items, setItems] = useState<SourceDetailItem[] | null>(null);

  useEffect(() => {
    let alive = true;
    getReportTimeline(stockCode)
      .then((d) => alive && setItems(d.items))
      .catch(() => alive && setItems([]));
    return () => {
      alive = false;
    };
  }, [stockCode]);

  if (items === null) return null; // 로딩 중엔 조용히(레이아웃 흔들림 방지)

  return (
    <section className="glass relative mt-6 p-5" data-section="evidence-timeline">
      <span className="file-tab absolute -top-[13px] left-5">
        <Clock size={13} /> 근거 타임라인
      </span>
      <p className="text-[12.5px] text-muted">
        여러 데이터 소스의 근거를 시간순으로 모았습니다. 어떤 근거가 언제 나왔는지 훑어볼 수 있어요.
      </p>

      {items.length === 0 ? (
        <p className="mt-3 text-[13px] text-muted">표시할 근거 이력이 없습니다.</p>
      ) : (
        <ol className="mt-4 space-y-4 border-l-2 border-black/10 pl-4">
          {items.map((item, i) => {
            const key = (item.source_type ?? "").toLowerCase();
            const meta = SOURCE_META[key];
            const dir = directionLabel(item.direction);
            const href = safeHttpUrl(item.evidence_url);
            return (
              <li key={i} className="relative">
                <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full brand-grad" />
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px]">
                  <span className="text-muted">{item.event_date ?? "날짜 미상"}</span>
                  <span className="inline-flex items-center gap-1 font-bold text-navy-soft">
                    {meta ? (
                      <>
                        <SourceIcon source={key} size={13} /> {meta.label}
                      </>
                    ) : (
                      (item.source_name ?? "근거")
                    )}
                  </span>
                  <span className={`pill ${dir.tone}`} style={{ padding: "1px 8px", fontSize: 11 }}>
                    {dir.label}
                  </span>
                </div>
                {item.title && <p className="mt-1 text-[14px] font-semibold text-navy-soft">{item.title}</p>}
                {item.summary && (
                  <p className="mt-0.5 text-[13px] leading-relaxed text-navy-soft">{item.summary}</p>
                )}
                {href && (
                  <a
                    href={href}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="mt-1 inline-block text-[12.5px] font-semibold text-sky-deep"
                  >
                    근거 원문 →
                  </a>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
