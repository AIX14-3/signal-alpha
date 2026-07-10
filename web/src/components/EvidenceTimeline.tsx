"use client";

import { ChevronDown, ChevronUp, Clock } from "lucide-react";
import { useEffect, useState } from "react";
import { getReportTimeline, type SourceDetailItem } from "@/lib/apiClient";
import { SourceIcon } from "@/components/SourceIcon";
import { directionLabel, safeHttpUrl, SOURCE_META } from "@/lib/format";

// 접힌 상태의 목록 높이(px). 근거가 20건 넘게 쌓이면 섹션이 화면을 다 잡아먹는다.
const COLLAPSED_HEIGHT = 360;

// S2 Evidence Timeline — 여러 소스의 근거를 한 종목에서 시간순(event_date)으로 모아 보여준다.
// 소스 상세가 "한 소스"만 보여주는 것과 달리, 소스를 가로질러 "언제 무슨 근거가 있었나"를 훑는다.
// 기본은 고정 높이 안에서 스크롤하고, "전체 펼치기"로 높이 제한을 푼다.
export function EvidenceTimeline({ stockCode }: { stockCode: string }) {
  const [items, setItems] = useState<SourceDetailItem[] | null>(null);
  const [expanded, setExpanded] = useState(false);

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
    <section className="glass relative mt-12 p-5" data-section="evidence-timeline">
      <span className="file-tab">
        <Clock size={13} /> 근거 타임라인
      </span>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-[12.5px] text-muted">
          여러 데이터 소스의 근거를 시간순으로 모았습니다. 어떤 근거가 언제 나왔는지 훑어볼 수 있어요.
        </p>
        {items.length > 0 && (
          <span className="shrink-0 text-[12.5px] font-semibold text-muted">총 {items.length}건</span>
        )}
      </div>

      {items.length === 0 ? (
        <p className="mt-3 text-[13px] text-muted">표시할 근거 이력이 없습니다.</p>
      ) : (
        <>
        {/* 스크롤은 바깥 래퍼가 맡는다. <ol> 에 직접 overflow 를 걸면 타임라인 점(dot)이
            ol 박스 왼쪽 밖(-left-[22px])에 있어 절반이 잘린다.
            왼쪽 여백 6px = 점이 잘리지 않고(왼쪽 끝 2px) 세로선 중심과 정확히 겹치는 값. */}
        <div
          className="mt-4 overflow-y-auto pl-[6px]"
          style={expanded ? undefined : { maxHeight: COLLAPSED_HEIGHT }}
          data-expanded={expanded ? "true" : "false"}
        >
        <ol className="space-y-4 border-l-2 border-black/10 pl-4">
          {items.map((item, i) => {
            const key = (item.source_type ?? "").toLowerCase();
            const meta = SOURCE_META[key];
            const dir = directionLabel(item.direction);
            const href = safeHttpUrl(item.evidence_url);
            return (
              <li key={i} className="relative">
                <span className="absolute -left-[22px] top-1.5 h-2.5 w-2.5 rounded-full brand-grad" />
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
        </div>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="mt-3 inline-flex items-center gap-1 text-[12.5px] font-semibold text-sky-deep hover:underline"
        >
          {expanded ? (
            <>
              접기 <ChevronUp size={14} aria-hidden="true" />
            </>
          ) : (
            <>
              전체 펼치기 <ChevronDown size={14} aria-hidden="true" />
            </>
          )}
        </button>
        </>
      )}
    </section>
  );
}
