"use client";

import { useEffect, useMemo, useState } from "react";
import {
  getJournalChart,
  getJournalTimeline,
  type Journal,
  type JournalChart,
  type JournalTimeline,
} from "@/lib/apiClient";
import { useJournalStore } from "@/stores/journalStore";
import { useToastStore } from "@/stores/toastStore";

const HORIZON_LABEL: Record<string, string> = { "7td": "7거래일", "30td": "30거래일" };
const VIEW_LABEL: Record<string, string> = {
  watch: "계속 관찰",
  research_more: "추가 확인 필요",
  not_relevant: "낮은 관련도",
};

function formatWon(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${Math.round(value).toLocaleString("ko-KR")}원`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(5).replace("-", "/");
}

function signText(pct: number): string {
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

/** 저널 카드 클릭 시 열리는 차트 패널 — 시리즈 로드 + 등락 설명 + SVG 라인차트. */
export function JournalChartPanel({ journal }: { journal: Journal }) {
  const [chart, setChart] = useState<JournalChart | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJournalChart(journal.journal_id)
      .then((d) => setChart(d.chart))
      .catch((e) => setError((e as Error).message));
  }, [journal.journal_id]);

  if (error) return <p className="mt-3 border-t border-line pt-3 text-[13px] text-red">{error}</p>;
  if (!chart) return <p className="mt-3 border-t border-line pt-3 text-[13px] text-muted">차트를 불러오는 중…</p>;
  if (chart.series.length === 0 || chart.base_price == null)
    return (
      <p className="mt-3 border-t border-line pt-3 text-[13px] text-muted">
        차트 데이터 준비 전입니다. 다음 시세 동기화(매일 아침) 후 표시됩니다.
      </p>
    );

  const pct = chart.change_pct_since_created ?? 0;
  const up = pct >= 0;
  const toneClass = up ? "text-red" : "text-sky-deep";

  return (
    <div className="mt-3 border-t border-line pt-3" data-flow="journal-chart">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-[13.5px] font-bold">
          작성 시점({formatDate(chart.base_trade_date)} · {formatWon(chart.base_price)}) 대비
        </span>
        <span className={`text-[20px] font-extrabold ${toneClass}`}>
          {up ? "▲" : "▼"} {signText(pct)}
        </span>
        <span className="text-[12.5px] text-muted">
          현재 {formatDate(chart.latest_trade_date)} 종가 {formatWon(chart.latest_price)}
        </span>
      </div>

      <PriceLineChart chart={chart} />

      <p className="mt-2 text-[12.5px] text-navy-soft">
        저널을 작성한 {formatDate(chart.base_trade_date)} 종가 {formatWon(chart.base_price)}를 기준으로, 최근
        종가는 {formatWon(chart.latest_price)}로 {Math.abs(pct).toFixed(2)}%{" "}
        {up ? "올랐습니다" : "내렸습니다"}.
        {journal.outcomes.length > 0
          ? " " +
            journal.outcomes
              .map((o) => `${HORIZON_LABEL[o.horizon] ?? o.horizon} 후 ${signText(o.change_pct)}로 확정되었습니다`)
              .join(", ") +
            "."
          : " 7·30거래일 변동은 만기 후 자동 확정됩니다."}{" "}
        본 차트는 매수·매도 추천이 아니라 판단 복기를 위한 기록입니다.
      </p>

      <details className="mt-2 text-[12px] text-muted">
        <summary className="cursor-pointer select-none">데이터 표 보기</summary>
        <div className="mt-1 max-h-40 overflow-y-auto">
          <table className="w-full text-left">
            <thead>
              <tr>
                <th className="pr-4 font-semibold">거래일</th>
                <th className="font-semibold">종가</th>
              </tr>
            </thead>
            <tbody>
              {chart.series.map((p) => (
                <tr key={p.trade_date}>
                  <td className="pr-4">{p.trade_date}</td>
                  <td>{formatWon(p.close)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

// 회고 경량 구조 — 계획 대비 결과 분류(중립 표현, 성과 판정 아님).
export const RETRO_OUTCOMES: { key: string; label: string; tone: string }[] = [
  { key: "as_planned", label: "계획대로", tone: "text-navy-soft" },
  { key: "unexpected_good", label: "예상보다 좋음", tone: "text-red" },
  { key: "unexpected_bad", label: "예상보다 아쉬움", tone: "text-sky-deep" },
];
const RETRO_LABEL: Record<string, string> = Object.fromEntries(
  RETRO_OUTCOMES.map((d) => [d.key, d.label]),
);
const RETRO_TONE: Record<string, string> = Object.fromEntries(
  RETRO_OUTCOMES.map((d) => [d.key, d.tone]),
);

// 회고 작성 여부 — 경량 구조(결과 분류·교훈) 중 하나라도 있으면 작성된 것으로 본다.
// 대기큐 카운트(mypage)와 카드 표시(여기)가 같은 정의를 쓰도록 단일 출처로 export.
export function hasRetrospective(journal: Journal): boolean {
  return Boolean(journal.retro_outcome_class || journal.retrospective_memo);
}

/**
 * 회고(결과 확인 후 복기) — outcome 이 확정된 저널에만 작성 가능(서버도 게이트).
 * 경량 구조: 결과 분류(retro_outcome_class) + 교훈 자유서술(retrospective_memo).
 * 카드/차트 어디서든 재사용(단일 컴포넌트로 상태 일원화).
 * autoEditSignal: 값이 증가하면(대기큐에서 눌릴 때마다) 편집 폼을 연다 — 리마운트 없이 재진입.
 */
export function RetrospectiveBlock({
  journal,
  autoEditSignal = 0,
}: {
  journal: Journal;
  autoEditSignal?: number;
}) {
  const update = useJournalStore((s) => s.update);
  const showToast = useToastStore((s) => s.show);
  const ready = journal.outcomes.length > 0;
  const [editing, setEditing] = useState(false);
  const [outcomeClass, setOutcomeClass] = useState(journal.retro_outcome_class ?? "");
  const [lesson, setLesson] = useState(journal.retrospective_memo ?? "");
  const [busy, setBusy] = useState(false);
  const hasRetro = hasRetrospective(journal);

  function beginEdit() {
    // 최신 저널 값으로 폼을 다시 채우고 편집 오픈.
    setOutcomeClass(journal.retro_outcome_class ?? "");
    setLesson(journal.retrospective_memo ?? "");
    setEditing(true);
  }

  // 대기큐에서 진입(신호 증가)하면 편집 폼을 연다. 리마운트가 아니라 신호 변화에 반응하므로
  // 다른 카드의 편집 중 텍스트를 버리지 않는다.
  useEffect(() => {
    if (autoEditSignal > 0 && ready) beginEdit();
    // beginEdit 은 최신 journal 로 재시드 — 의도적으로 신호에만 반응.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoEditSignal]);

  async function save() {
    setBusy(true);
    try {
      await update(journal.journal_id, {
        retro_outcome_class: outcomeClass,
        retrospective_memo: lesson,
      });
      setEditing(false);
      showToast("회고를 저장했습니다.", "success");
    } catch (e) {
      showToast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="mt-3.5 rounded-[14px] border p-3.5"
      style={{ background: "#faf7ff", borderColor: "#eee7fb" }}
      data-flow="journal-retrospective"
    >
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-bold text-sky-deep">
          회고 <span className="font-normal text-muted">— 결과 확인 후 복기</span>
        </span>
        <div className="flex items-center gap-2.5">
          {hasRetro && !editing && journal.retro_outcome_class && (
            <span
              className={`pill flat text-[11px] font-bold ${RETRO_TONE[journal.retro_outcome_class] ?? ""}`}
              style={{ padding: "2px 10px" }}
            >
              {RETRO_LABEL[journal.retro_outcome_class] ?? journal.retro_outcome_class}
            </span>
          )}
          {ready && !editing && (
            <button
              type="button"
              data-flow="journal-retro-edit"
              onClick={beginEdit}
              className="text-[12.5px] font-semibold text-sky-deep"
            >
              {hasRetro ? "수정" : "회고 남기기"}
            </button>
          )}
        </div>
      </div>

      {!ready ? (
        <p className="mt-1 text-[12.5px] text-muted">변동(7·30거래일)이 확정되면 회고를 남길 수 있습니다.</p>
      ) : editing ? (
        <div className="mt-2 space-y-2">
          <div className="flex flex-wrap gap-1.5" data-flow="journal-retro-outcome">
            {RETRO_OUTCOMES.map((d) => (
              <button
                key={d.key}
                type="button"
                onClick={() => setOutcomeClass(outcomeClass === d.key ? "" : d.key)}
                className={`pill flat text-[12px] ${outcomeClass === d.key ? "!border-sky !text-sky-deep font-bold" : ""}`}
                style={{ padding: "4px 10px" }}
              >
                {d.label}
              </button>
            ))}
          </div>
          <textarea
            value={lesson}
            onChange={(e) => setLesson(e.target.value)}
            rows={2}
            className="card w-full px-4 py-2.5 text-[13px] outline-none focus:border-sky"
            placeholder="예상과 달랐던 점, 다음에 참고할 점을 남겨보세요."
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void save()}
              disabled={busy}
              className="brand-grad rounded-full px-4 py-1.5 text-[12.5px] font-bold text-white disabled:opacity-60"
            >
              {busy ? "저장 중…" : "저장"}
            </button>
            <button type="button" onClick={() => setEditing(false)} className="text-[12.5px] text-muted">
              취소
            </button>
          </div>
          <p className="text-[11px] text-muted">기록·학습을 위한 복기이며 성과 평가가 아닙니다.</p>
        </div>
      ) : hasRetro ? (
        <div className="mt-1.5">
          {journal.retrospective_memo && (
            <p className="whitespace-pre-wrap text-[13px] text-navy-soft">{journal.retrospective_memo}</p>
          )}
        </div>
      ) : (
        <p className="mt-1 text-[12.5px] text-muted">
          변동이 확정됐습니다. 예상과 달랐던 점을 회고로 남겨보세요.
        </p>
      )}
    </div>
  );
}

const W = 640;
const H = 220;
const PAD = { top: 14, right: 14, bottom: 26, left: 56 };

function PriceLineChart({ chart }: { chart: JournalChart }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const points = chart.series.filter((p) => p.close != null);

  const geom = useMemo(() => {
    const closes = points.map((p) => p.close as number);
    const lo = Math.min(...closes, chart.base_price ?? Infinity);
    const hi = Math.max(...closes, chart.base_price ?? -Infinity);
    const span = hi - lo || hi || 1;
    const yMin = lo - span * 0.06;
    const yMax = hi + span * 0.06;
    const x = (i: number) =>
      PAD.left + (points.length <= 1 ? 0 : (i * (W - PAD.left - PAD.right)) / (points.length - 1));
    const y = (v: number) => PAD.top + ((yMax - v) / (yMax - yMin)) * (H - PAD.top - PAD.bottom);
    const baseIndex = chart.base_trade_date
      ? points.findIndex((p) => p.trade_date === chart.base_trade_date)
      : -1;
    return { x, y, yMin, yMax, baseIndex };
  }, [points, chart.base_price, chart.base_trade_date]);

  if (points.length < 2) return null;

  const { x, y, yMin, yMax, baseIndex } = geom;
  const pct = chart.change_pct_since_created ?? 0;
  const toneClass = pct >= 0 ? "text-red" : "text-sky-deep";
  const linePath = points.map((p, i) => `${x(i).toFixed(1)},${y(p.close as number).toFixed(1)}`).join(" ");
  const areaPath = `${linePath} ${x(points.length - 1).toFixed(1)},${H - PAD.bottom} ${x(0).toFixed(1)},${H - PAD.bottom}`;
  const last = points[points.length - 1];
  const hover = hoverIndex != null ? points[hoverIndex] : null;

  function onPointerMove(e: React.PointerEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const ratio = (px - PAD.left) / (W - PAD.left - PAD.right);
    const index = Math.round(ratio * (points.length - 1));
    setHoverIndex(Math.max(0, Math.min(points.length - 1, index)));
  }

  return (
    <div className={`relative mt-2 ${toneClass}`}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="작성 시점 이후 종가 추이 차트"
        onPointerMove={onPointerMove}
        onPointerLeave={() => setHoverIndex(null)}
      >
        {/* 눈금(상/하한) — 눈에 띄지 않게 */}
        {[yMax, yMin].map((v, i) => {
          const gy = i === 0 ? PAD.top : H - PAD.bottom;
          return (
            <g key={i}>
              <line x1={PAD.left} x2={W - PAD.right} y1={gy} y2={gy} stroke="#e2e8f0" strokeWidth={1} />
              <text x={PAD.left - 6} y={gy + 4} textAnchor="end" fontSize={11} fill="#64748b">
                {Math.round(i === 0 ? yMax : yMin).toLocaleString("ko-KR")}
              </text>
            </g>
          );
        })}

        {/* 작성 시점 기준선(가격 수평 + 날짜 수직) */}
        {chart.base_price != null && (
          <line
            x1={PAD.left}
            x2={W - PAD.right}
            y1={y(chart.base_price)}
            y2={y(chart.base_price)}
            stroke="#94a3b8"
            strokeWidth={1}
            strokeDasharray="4 4"
          />
        )}
        {baseIndex >= 0 && (
          <g>
            <line
              x1={x(baseIndex)}
              x2={x(baseIndex)}
              y1={PAD.top}
              y2={H - PAD.bottom}
              stroke="#94a3b8"
              strokeWidth={1}
              strokeDasharray="4 4"
            />
            <text x={x(baseIndex)} y={H - 8} textAnchor="middle" fontSize={11} fill="#64748b">
              작성 {formatDate(chart.base_trade_date)}
            </text>
          </g>
        )}

        {/* 종가 라인 + 은은한 면 (등락 색 = currentColor) */}
        <polygon points={areaPath} fill="currentColor" opacity={0.08} />
        <polyline points={linePath} fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" />

        {/* 작성 시점·최신 종가 포인트(흰 테두리로 라인과 분리) */}
        {baseIndex >= 0 && (
          <circle cx={x(baseIndex)} cy={y(points[baseIndex].close as number)} r={4} fill="#94a3b8" stroke="#fff" strokeWidth={2} />
        )}
        <circle cx={x(points.length - 1)} cy={y(last.close as number)} r={4} fill="currentColor" stroke="#fff" strokeWidth={2} />
        <text
          x={Math.min(x(points.length - 1), W - PAD.right)}
          y={Math.max(y(last.close as number) - 8, 12)}
          textAnchor="end"
          fontSize={11.5}
          fontWeight={700}
          fill="#334155"
        >
          {Math.round(last.close as number).toLocaleString("ko-KR")}
        </text>

        {/* x축 시작/끝 날짜 */}
        <text x={PAD.left} y={H - 8} fontSize={11} fill="#64748b">{formatDate(points[0].trade_date)}</text>
        <text x={W - PAD.right} y={H - 8} textAnchor="end" fontSize={11} fill="#64748b">
          {formatDate(last.trade_date)}
        </text>

        {/* 호버 크로스헤어 */}
        {hover && hoverIndex != null && (
          <g>
            <line x1={x(hoverIndex)} x2={x(hoverIndex)} y1={PAD.top} y2={H - PAD.bottom} stroke="#cbd5e1" strokeWidth={1} />
            <circle cx={x(hoverIndex)} cy={y(hover.close as number)} r={4.5} fill="currentColor" stroke="#fff" strokeWidth={2} />
          </g>
        )}
      </svg>

      {hover && hoverIndex != null && chart.base_price != null && (
        <div
          className="pointer-events-none absolute -top-1 rounded-[8px] border border-line bg-surface px-2.5 py-1.5 text-[11.5px] shadow-sm"
          style={{
            left: `${(x(hoverIndex) / W) * 100}%`,
            transform: `translateX(${hoverIndex > points.length / 2 ? "-105%" : "5%"})`,
          }}
        >
          <div className="font-semibold text-navy">{hover.trade_date}</div>
          <div className="text-navy-soft">{formatWon(hover.close)}</div>
          <div className={toneClass}>
            기준 대비 {signText(((hover.close as number) - chart.base_price) / chart.base_price * 100)}
          </div>
        </div>
      )}
    </div>
  );
}

/** 종목별 저널 타임라인 — 한 장의 가격 차트 위에 저널 작성 시점을 번호 마커로 표시. */
export function JournalTimelinePanel({ stockCode }: { stockCode: string }) {
  const [timeline, setTimeline] = useState<JournalTimeline | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJournalTimeline(stockCode)
      .then(setTimeline)
      .catch((e) => setError((e as Error).message));
  }, [stockCode]);

  if (error) return <p className="text-[13px] text-red">{error}</p>;
  if (!timeline) return <p className="text-[13px] text-muted">타임라인을 불러오는 중…</p>;
  if (timeline.series.length < 2)
    return (
      <p className="text-[13px] text-muted">
        {timeline.stock.stock_name ?? stockCode}: 차트 데이터 준비 전입니다(다음 시세 동기화 후 표시).
      </p>
    );

  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-bold">
          {timeline.stock.stock_name ?? timeline.stock.stock_code}{" "}
          <span className="text-[12px] font-normal text-muted">{timeline.stock.stock_code}</span>
        </span>
        <span className="text-[12.5px] text-muted">
          저널 {timeline.journals.length}건 · 최근 종가 {formatWon(timeline.latest_price)}
        </span>
      </div>

      <TimelineChart timeline={timeline} />

      <ol className="mt-2 space-y-1 text-[12.5px]">
        {timeline.journals.map((marker, index) => (
          <li key={marker.journal_id} className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-bold text-sky-deep">{index + 1}.</span>
            <span className="text-muted">{formatDate(marker.trade_date ?? marker.created_at)}</span>
            <span className="pill flat" style={{ padding: "1px 7px", fontSize: 11 }}>
              {VIEW_LABEL[marker.user_view] ?? marker.user_view}
            </span>
            {marker.price != null && <span className="text-muted">{formatWon(marker.price)}</span>}
            {marker.memo && <span className="text-navy-soft">{marker.memo}</span>}
            {marker.retrospective_memo && (
              <span className="text-muted">↩ 회고: {marker.retrospective_memo}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

function TimelineChart({ timeline }: { timeline: JournalTimeline }) {
  const points = timeline.series.filter((p) => p.close != null);
  const geom = useMemo(() => {
    const closes = points.map((p) => p.close as number);
    const lo = Math.min(...closes);
    const hi = Math.max(...closes);
    const span = hi - lo || hi || 1;
    const yMin = lo - span * 0.08;
    const yMax = hi + span * 0.12; // 마커 번호 라벨 자리
    const x = (i: number) =>
      PAD.left + (points.length <= 1 ? 0 : (i * (W - PAD.left - PAD.right)) / (points.length - 1));
    const y = (v: number) => PAD.top + ((yMax - v) / (yMax - yMin)) * (H - PAD.top - PAD.bottom);
    const indexByDate = new Map(points.map((p, i) => [p.trade_date, i]));
    return { x, y, yMin, yMax, indexByDate };
  }, [points]);

  if (points.length < 2) return null;
  const { x, y, yMin, yMax, indexByDate } = geom;
  const linePath = points.map((p, i) => `${x(i).toFixed(1)},${y(p.close as number).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];

  return (
    <div className="mt-2 text-navy">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="저널 작성 시점이 표시된 종가 추이 차트">
        {[yMax, yMin].map((v, i) => {
          const gy = i === 0 ? PAD.top : H - PAD.bottom;
          return (
            <g key={i}>
              <line x1={PAD.left} x2={W - PAD.right} y1={gy} y2={gy} stroke="#e2e8f0" strokeWidth={1} />
              <text x={PAD.left - 6} y={gy + 4} textAnchor="end" fontSize={11} fill="#64748b">
                {Math.round(v).toLocaleString("ko-KR")}
              </text>
            </g>
          );
        })}

        <polyline points={linePath} fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" opacity={0.75} />

        {/* 저널 작성 시점 마커(번호) — 같은 거래일의 저널들은 한 점에 번호를 묶어(1·2) 겹침 방지.
            상세 내용은 아래 목록과 네이티브 툴팁으로 제공 */}
        {(() => {
          const groups = new Map<number, number[]>(); // pointIndex → marker index[]
          timeline.journals.forEach((marker, index) => {
            const pointIndex = marker.trade_date != null ? indexByDate.get(marker.trade_date) : undefined;
            if (pointIndex == null || marker.price == null) return;
            groups.set(pointIndex, [...(groups.get(pointIndex) ?? []), index]);
          });
          return [...groups.entries()].map(([pointIndex, markerIndices]) => {
            const first = timeline.journals[markerIndices[0]];
            const label = markerIndices.map((i) => i + 1).join("·");
            const tooltip = markerIndices
              .map((i) => {
                const m = timeline.journals[i];
                return `${i + 1}. ${formatDate(m.trade_date)} · ${VIEW_LABEL[m.user_view] ?? m.user_view}${m.memo ? ` — ${m.memo}` : ""}`;
              })
              .join("\n");
            return (
              <g key={pointIndex}>
                <title>{tooltip}</title>
                <line
                  x1={x(pointIndex)}
                  x2={x(pointIndex)}
                  y1={y(first.price as number)}
                  y2={H - PAD.bottom}
                  stroke="#94a3b8"
                  strokeWidth={1}
                  strokeDasharray="3 3"
                />
                <circle cx={x(pointIndex)} cy={y(first.price as number)} r={5} fill="#0284c7" stroke="#fff" strokeWidth={2} />
                <text
                  x={x(pointIndex)}
                  y={y(first.price as number) - 10}
                  textAnchor="middle"
                  fontSize={11.5}
                  fontWeight={700}
                  fill="#0284c7"
                >
                  {label}
                </text>
              </g>
            );
          });
        })()}

        <circle cx={x(points.length - 1)} cy={y(last.close as number)} r={4} fill="currentColor" stroke="#fff" strokeWidth={2} />
        <text x={PAD.left} y={H - 8} fontSize={11} fill="#64748b">{formatDate(points[0].trade_date)}</text>
        <text x={W - PAD.right} y={H - 8} textAnchor="end" fontSize={11} fill="#64748b">
          {formatDate(last.trade_date)}
        </text>
      </svg>
    </div>
  );
}
