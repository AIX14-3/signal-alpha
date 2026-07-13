"use client";

import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import {
  getReportPrecedents,
  type PrecedentExample,
  type PrecedentGroup,
  type ReportPrecedents,
} from "@/lib/apiClient";
import { directionLabel, sourceLabel } from "@/lib/format";
import { SourceIcon } from "@/components/SourceIcon";

// S6 과거 유사 사례 — "이런 신호 뒤 주가가 어땠나"를 event_study_panel 실측 20일 수익률로
// 문서형(제안서) 레이아웃에 보여준다. 값은 서술 참고용(인과 미래보장 아님). "유사"는 결정론
// (같은 종목 소스 방향)이며, 프로덕션에서 임베딩 코사인 recall 로 교체 가능(계약 동일).

function pctText(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

function retClass(v: number | null | undefined): string {
  if (v == null) return "text-muted";
  // 한국 시장 관례 — 상승=빨강, 하락=파랑(캔들 KR_UP/KR_DOWN 과 동일).
  return v > 0 ? "text-[#ef4444]" : v < 0 ? "text-[#3b82f6]" : "text-navy-soft";
}

export function PrecedentSection({ stockCode }: { stockCode: string }) {
  const [data, setData] = useState<ReportPrecedents | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    getReportPrecedents(stockCode)
      .then((d) => alive && setData(d))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [stockCode]);

  if (failed) return null;
  if (!data) return null;

  const hasAny = data.groups.length > 0 || data.recent.length > 0;

  return (
    <section className="glass relative mt-12 p-5" data-section="precedents">
      <span className="file-tab">
        <FileText size={13} /> 사례
      </span>
      <h2 className="text-[18px] font-bold">과거 유사 사례   이후 {data.horizon_days}일 실측</h2>
      <p className="mt-1 text-[12.5px] text-muted">
        이 종목에서 같은 소스 방향의 신호가 과거에 발생한 뒤, {data.horizon_days}거래일 동안 주가가
        실제로 어떻게 움직였는지 집계한 <b>참고 통계</b>입니다. 인과관계나 미래 수익을 보장하지 않습니다.
      </p>

      {!hasAny ? (
        <p className="mt-4 text-[13.5px] text-navy-soft">아직 집계할 과거 사례가 없습니다.</p>
      ) : (
        <>
          {data.groups.length > 0 && (
            <div className="mt-4 space-y-2">
              {data.groups.map((g, i) => (
                <PrecedentGroupRow key={`${g.source}-${g.direction}-${i}`} g={g} />
              ))}
            </div>
          )}

          {data.recent.length > 0 && (
            <div className="mt-5">
              <div className="text-[13px] font-bold text-navy-soft">최근 사례</div>
              <ul className="mt-2 space-y-1.5">
                {data.recent.map((e, i) => (
                  <PrecedentExampleRow key={`${e.date}-${e.source}-${i}`} e={e} />
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <p className="mt-4 border-t border-line pt-3 text-[11.5px] text-muted">
        ※ 과거 유사 사례의 실측 {data.horizon_days}일 수익률 기준. <b>시장대비</b>는 코스피20
        벤치마크를 뺀 초과수익입니다. 표본 기간 편향이 있을 수 있어 판단 보조로만 활용하세요.
      </p>
    </section>
  );
}

function PrecedentGroupRow({ g }: { g: PrecedentGroup }) {
  const dir = directionLabel(g.direction);
  const src = g.source ?? "price";
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[12px] bg-surface-2 px-4 py-3">
      <span className="flex items-center gap-1.5 font-semibold">
        <SourceIcon source={src} size={16} className="text-navy-soft" />
        {sourceLabel(src)}
      </span>
      {/* 방향 배지 — 한국 관례색(dir.pill): 긍정=빨강/부정=파랑/불일치=보라/중립=회색. */}
      <span className={`pill ${dir.pill}`} style={{ padding: "2px 8px", fontSize: 11.5 }}>
        {dir.label}
      </span>
      <span className="text-[12.5px] text-muted">과거 {g.count ?? 0}회</span>
      <span className="ml-auto flex items-baseline gap-3">
        <span className="text-[12px] text-muted">
          시장대비{" "}
          <b className={`text-[15px] ${retClass(g.avg_abnormal_return)}`}>
            {pctText(g.avg_abnormal_return)}
          </b>
        </span>
        <span className="text-[12px] text-muted">
          실측 평균 <b className={retClass(g.avg_return)}>{pctText(g.avg_return)}</b>
        </span>
        <span className="text-[12px] text-muted">
          상승률 <b className="text-navy-soft">{g.win_rate == null ? "—" : `${Math.round(g.win_rate)}%`}</b>
        </span>
      </span>
    </div>
  );
}

function PrecedentExampleRow({ e }: { e: PrecedentExample }) {
  const dir = directionLabel(e.direction);
  const src = e.source ?? "price";
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 text-[12.5px]">
      <span className="text-muted">{e.date ?? "—"}</span>
      <span className="flex items-center gap-1 font-semibold text-navy-soft">
        <SourceIcon source={src} size={13} /> {sourceLabel(src)}
      </span>
      <span className="text-muted">{dir.label}</span>
      {e.title && <span className="truncate text-navy-soft">  {e.title}</span>}
      <span className="ml-auto text-muted">
        시장대비 <b className={retClass(e.abnormal_return)}>{pctText(e.abnormal_return)}</b>
      </span>
    </li>
  );
}
