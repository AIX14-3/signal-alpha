"use client";

import { useEffect, useRef, useState } from "react";
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  LineSeries,
} from "lightweight-charts";
import { LineChart } from "lucide-react";
import { getReportPrices, type PriceSeries } from "@/lib/apiClient";

// item 6 — 리포트 상단 주가 차트 + 시세. lightweight-charts. SSR 회피 위해 상위에서
// next/dynamic(ssr:false)로 로드한다. 차트 모양(영역/라인/캔들)과 기간(분/일/월/년)을
// 사용자가 버튼으로 유동적으로 바꿀 수 있다. 실패 시 결정론 합성 시세로 폴백(is_demo).
const TFS: [string, string][] = [
  ["min", "분"],
  ["day", "일"],
  ["month", "월"],
  ["year", "년"],
];

type ChartType = "area" | "line" | "candle";
const CHART_TYPES: [ChartType, string][] = [
  ["area", "영역"],
  ["line", "라인"],
  ["candle", "캔들"],
];

const VIOLET = "#7c3aed";
// 캔들은 한국 시장 관례(상승=빨강, 하락=파랑)를 따른다.
const KR_UP = "#ef4444";
const KR_DOWN = "#3b82f6";

export function StockChart({ stockCode, stockName }: { stockCode: string; stockName?: string | null }) {
  const [tf, setTf] = useState("day");
  const [chartType, setChartType] = useState<ChartType>("area");
  const [data, setData] = useState<PriceSeries | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    getReportPrices(stockCode, tf)
      .then((d) => alive && setData(d))
      .catch(() => alive && setData(null));
    return () => {
      alive = false;
    };
  }, [stockCode, tf]);

  useEffect(() => {
    const el = boxRef.current;
    if (!el || !data) return;
    const chart = createChart(el, {
      height: 260,
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#64748b",
        fontSize: 11,
      },
      grid: { horzLines: { color: "rgba(124,58,237,.06)" }, vertLines: { color: "rgba(124,58,237,.04)" } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: tf === "min" },
    });

    if (chartType === "candle") {
      // 캔들 — OHLC 전체. 한국 관례 색(상승 빨강 / 하락 파랑).
      const series = chart.addSeries(CandlestickSeries, {
        upColor: KR_UP,
        downColor: KR_DOWN,
        borderVisible: false,
        wickUpColor: KR_UP,
        wickDownColor: KR_DOWN,
      });
      series.setData(data.bars as never);
    } else if (chartType === "line") {
      // 라인 — 브랜드 바이올렛 종가 라인(채움 없음).
      const series = chart.addSeries(LineSeries, {
        color: VIOLET,
        lineWidth: 2,
        priceLineVisible: false,
        crosshairMarkerBorderColor: VIOLET,
        crosshairMarkerBackgroundColor: "#a855f7",
      });
      series.setData(data.bars.map((b) => ({ time: b.time, value: b.close })) as never);
    } else {
      // 영역(기본) — 토스식. 바이올렛 종가 라인 + 상단→하단 그라데이션 채움.
      const series = chart.addSeries(AreaSeries, {
        lineColor: VIOLET,
        topColor: "rgba(124,58,237,.28)",
        bottomColor: "rgba(124,58,237,.02)",
        lineWidth: 2,
        priceLineVisible: false,
        crosshairMarkerBorderColor: VIOLET,
        crosshairMarkerBackgroundColor: "#a855f7",
      });
      series.setData(data.bars.map((b) => ({ time: b.time, value: b.close })) as never);
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data, tf, chartType]);

  const up = (data?.change ?? 0) >= 0;

  return (
    <section className="glass relative mt-6 p-5" data-section="stock-chart">
      <span className="file-tab absolute -top-[13px] left-5">
        <LineChart size={13} /> 시세
      </span>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-[13px] text-muted">{stockName ?? stockCode} 시세</div>
          <div className="mt-0.5 flex items-baseline gap-2">
            <span className="text-[28px] font-extrabold leading-none">
              {data ? data.last_price.toLocaleString("ko-KR") : "–"}
              <span className="ml-1 text-[13px] font-semibold text-muted">원</span>
            </span>
            {data && (
              <span className={`text-[14px] font-bold ${up ? "text-[#10b981]" : "text-[#ef4444]"}`}>
                {up ? "▲" : "▼"} {Math.abs(data.change).toLocaleString("ko-KR")} (
                {Math.abs(data.change_pct)}%)
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* 차트 모양 토글 — 영역/라인/캔들 */}
          <div className="flex gap-1 rounded-full bg-surface-2 p-0.5" data-control="chart-type">
            {CHART_TYPES.map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setChartType(key)}
                aria-pressed={chartType === key}
                className={`rounded-full px-3 py-1 text-[12px] font-semibold transition ${
                  chartType === key ? "bg-white text-sky-deep shadow-sm" : "text-muted hover:text-navy"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {/* 기간 토글 — 분/일/월/년 */}
          <div className="flex gap-1" data-control="chart-timeframe">
            {TFS.map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setTf(key)}
                aria-pressed={tf === key}
                className={`rounded-full px-3 py-1 text-[12.5px] font-semibold ${
                  tf === key ? "brand-grad text-white" : "border border-line text-navy-soft hover:border-navy"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div ref={boxRef} className="mt-3 w-full" style={{ height: 260 }} />
      {data?.is_demo ? (
        <p className="mt-2 text-[11.5px] text-muted">* 데모용 예시 시세입니다(실데이터 아님).</p>
      ) : data ? (
        <p className="mt-2 text-[11.5px] text-muted">출처: Yahoo Finance · 지연 시세</p>
      ) : null}
    </section>
  );
}
