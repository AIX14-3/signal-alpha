"use client";

import { useEffect, useMemo, useState } from "react";
import { getJournalChart, type Journal, type JournalChart } from "@/lib/apiClient";

const HORIZON_LABEL: Record<string, string> = { "7td": "7거래일", "30td": "30거래일" };

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
