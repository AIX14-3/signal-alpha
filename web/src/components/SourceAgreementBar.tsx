"use client";

import { Compass } from "lucide-react";
import type { ReportSource } from "@/lib/apiClient";
import { agreementLabel } from "@/lib/format";

// 소스 간 일치도 — "여러 소스가 같은 방향을 가리키는가"를 방향별 개수로 구체적으로 보여준다.
// (추상적 % 대신 "N개 소스 중 M개가 ○○ 방향" + 방향 분포 막대) → 종합 점수(방향의 세기)와 구분.
// 색 = 한국 관례(긍정=빨강 / 부정=파랑). 혼조·중립은 무채색. 칩(cls)·게이지 바(color) 동일 기준.
const DIRECTIONS: { key: string; label: string; cls: string; color: string }[] = [
  { key: "positive", label: "긍정", cls: "dir-pos", color: "rgba(239,68,68,.85)" },
  { key: "mixed", label: "혼조", cls: "flat", color: "rgba(148,163,184,.8)" },
  { key: "neutral", label: "중립", cls: "flat", color: "rgba(148,163,184,.5)" },
  { key: "negative", label: "부정", cls: "dir-neg", color: "rgba(59,130,246,.85)" },
];

export function SourceAgreementBar({
  agreement,
  sources,
}: {
  agreement?: string | null;
  sources: ReportSource[];
}) {
  const counted = sources.filter((s) => s.direction);
  const total = counted.length;
  const counts: Record<string, number> = { positive: 0, mixed: 0, neutral: 0, negative: 0 };
  for (const s of counted) {
    const d = (s.direction ?? "").toLowerCase();
    if (d in counts) counts[d] += 1;
    else counts.neutral += 1;
  }
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
  const topLabel = DIRECTIONS.find((x) => x.key === top?.[0])?.label ?? "중립";

  return (
    <section className="glass relative mt-12 p-5" data-section="source-agreement">
      <span className="file-tab">
        <Compass size={13} /> 소스 간 일치도
      </span>
      <div className="flex items-center justify-end">
        <span className="pill flat" style={{ padding: "3px 9px", fontSize: 12 }}>
          일치도 {agreementLabel(agreement)}
        </span>
      </div>
      {total > 0 ? (
        <>
          <p className="mt-1.5 text-[13.5px] text-navy-soft">
            분석된 <b>{total}개</b> 소스 중 <b>{top?.[1] ?? 0}개</b>가 <b>{topLabel}</b> 방향을
            가리킵니다.
          </p>
          <div className="mt-3 flex h-3 w-full overflow-hidden rounded-full bg-surface-2">
            {DIRECTIONS.map((d) =>
              counts[d.key] > 0 ? (
                <div
                  key={d.key}
                  style={{ width: `${(counts[d.key] / total) * 100}%`, background: d.color }}
                  title={`${d.label} ${counts[d.key]}개`}
                />
              ) : null,
            )}
          </div>
          <div className="mt-2.5 flex flex-wrap gap-2">
            {DIRECTIONS.map((d) =>
              counts[d.key] > 0 ? (
                <span
                  key={d.key}
                  className={`pill ${d.cls}`}
                  style={{ padding: "2px 9px", fontSize: 12 }}
                >
                  {d.label} {counts[d.key]}
                </span>
              ) : null,
            )}
          </div>
        </>
      ) : (
        <p className="mt-1.5 text-[13px] text-muted">아직 방향을 판단할 소스가 충분하지 않습니다.</p>
      )}
    </section>
  );
}
