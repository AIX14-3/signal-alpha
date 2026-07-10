"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  LineSeries,
  LineStyle,
} from "lightweight-charts";
import { LineChart } from "lucide-react";
import { getReportPrices, type PriceSeries } from "@/lib/apiClient";

// item 6 — 리포트 상단 주가 차트 + 시세. lightweight-charts. SSR 회피 위해 상위에서
// next/dynamic(ssr:false)로 로드한다. 차트 모양(영역/라인/캔들)과 기간(분/일/월/년)을
// 사용자가 버튼으로 유동적으로 바꿀 수 있다. 실패 시 결정론 합성 시세로 폴백(is_demo).
//
// 갱신: 서버는 호출 때마다 Yahoo 를 조회한다(캐시 없음). 장중에만 주기적으로 다시 받아
// 마지막 봉과 현재가를 갱신한다. 차트 인스턴스는 재생성하지 않는다(확대/스크롤 보존).
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

// 시세는 한국 시장 관례(상승=빨강, 하락=파랑)를 따른다. 캔들 색과 등락 표기에 함께 쓴다.
// (제품 전반의 방향 의미색 up=초록/down=빨강과는 별개 — 여기는 '시세' 문맥이다.)
const KR_UP = "#ef4444";
const KR_DOWN = "#3b82f6";

// 분봉은 자주, 그 외는 느슨하게. 장 종료 후에는 값이 안 변하므로 폴링 자체를 멈춘다.
const REFRESH_MS: Record<string, number> = { min: 20_000, day: 60_000, month: 300_000, year: 300_000 };

/** KRX 정규장(평일 09:00–15:35 KST) 여부. 장 밖에서는 폴링하지 않는다(무의미한 호출·쿼터 낭비). */
function isMarketOpen(now = new Date()): boolean {
  const kst = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  const day = kst.getDay();
  if (day === 0 || day === 6) return false;
  const minutes = kst.getHours() * 60 + kst.getMinutes();
  return minutes >= 9 * 60 && minutes <= 15 * 60 + 35;
}

// 현재가 수평선 + 우측 축 라벨. 폴링 갱신 때 눈에 띄게 움직이는 요소가 이것뿐이다(일봉에서는
// 오늘 봉 하나가 y축의 0.2% 남짓 움직여 사실상 안 보인다). 캔들은 라이브러리 기본값으로 이미 켜져 있다.
function priceLineOptions(color: string) {
  return {
    priceLineVisible: true,
    priceLineColor: color,
    priceLineStyle: LineStyle.Dashed,
    lastValueVisible: true,
  };
}

// lightweight-charts 에는 타임존 개념이 없다. 숫자 time 은 UTCTimestamp 로 보고 그대로 UTC 로
// 눈금을 찍는다. 분봉은 유닉스 초를 그대로 넘기므로 10:50 장중이 01:50 로 보였다. 한국 증시
// 시간을 읽히게 하려고 KST(고정 +9h, 서머타임 없음) 만큼 옮겨서 넘긴다. 일/월/년 봉은 서버가
// 'YYYY-MM-DD' 문자열을 주므로 해당 없음.
const KST_OFFSET_SEC = 9 * 60 * 60;

function toSeriesData(bars: PriceSeries["bars"], chartType: ChartType, intraday: boolean) {
  const at = (bar: PriceSeries["bars"][number]) =>
    intraday && typeof bar.time === "number" ? bar.time + KST_OFFSET_SEC : bar.time;
  if (chartType === "candle") return bars.map((b) => ({ ...b, time: at(b) })) as never;
  return bars.map((b) => ({ time: at(b), value: b.close })) as never;
}

export function StockChart({ stockCode, stockName }: { stockCode: string; stockName?: string | null }) {
  // 장중에는 오늘 흐름(분봉)을 먼저 보여 준다. 일봉 6개월에서는 오늘 봉 하나가 움직여 봐야
  // y축 범위의 0.2% 남짓이라, "장중 갱신"이라 써 놓고도 차트는 멈춘 것처럼 보인다.
  const [tf, setTf] = useState(() => (isMarketOpen() ? "min" : "day"));
  const [chartType, setChartType] = useState<ChartType>("area");
  const [data, setData] = useState<PriceSeries | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area" | "Line" | "Candlestick"> | null>(null);
  // 기간·차트 모양을 바꾼 직후 한 번만 화면에 맞춘다. 폴링 갱신 때 맞추면 사용자의 확대가 풀린다.
  const shouldFitRef = useRef(true);

  const fetchPrices = useCallback(
    async (signal?: { aborted: boolean }) => {
      try {
        const d = await getReportPrices(stockCode, tf);
        if (signal?.aborted) return;
        setData(d);
        setUpdatedAt(new Date());
      } catch {
        if (!signal?.aborted) setData(null);
      }
    },
    [stockCode, tf],
  );

  // 최초 로드 + 기간 변경 시 재조회.
  useEffect(() => {
    const token = { aborted: false };
    shouldFitRef.current = true;
    void fetchPrices(token);
    return () => {
      token.aborted = true;
    };
  }, [fetchPrices]);

  // 장중 주기 갱신. 탭이 백그라운드면 멈추고, 돌아오면 즉시 한 번 받아온다.
  useEffect(() => {
    const period = REFRESH_MS[tf] ?? 60_000;
    let timer: number | undefined;

    const tick = () => {
      if (document.visibilityState !== "visible" || !isMarketOpen()) return;
      void fetchPrices();
    };
    const start = () => {
      window.clearInterval(timer);
      timer = window.setInterval(tick, period);
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        tick();
        start();
      } else {
        window.clearInterval(timer);
      }
    };

    start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [tf, fetchPrices]);

  // 라인/영역도 시세 관례를 따라 등락 색으로 그린다(상승 빨강 / 하락 파랑).
  // 생성 효과는 ref 로 읽는다 — 색을 의존성에 넣으면 등락이 뒤집힐 때마다 차트가 재생성된다.
  const trendColor = (data?.change ?? 0) >= 0 ? KR_UP : KR_DOWN;
  const trendColorRef = useRef(trendColor);
  trendColorRef.current = trendColor;

  // 차트 인스턴스 생성 — 기간/모양이 바뀔 때만. 데이터 갱신으로는 재생성하지 않는다.
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
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
      seriesRef.current = chart.addSeries(CandlestickSeries, {
        upColor: KR_UP,
        downColor: KR_DOWN,
        borderVisible: false,
        wickUpColor: KR_UP,
        wickDownColor: KR_DOWN,
      });
    } else if (chartType === "line") {
      // 라인 — 등락 색 종가 라인(채움 없음).
      seriesRef.current = chart.addSeries(LineSeries, {
        color: trendColorRef.current,
        lineWidth: 2,
        ...priceLineOptions(trendColorRef.current),
      });
    } else {
      // 영역(기본) — 등락 색 종가 라인 + 상단→하단 그라데이션 채움.
      const rgb = trendColorRef.current === KR_UP ? "239,68,68" : "59,130,246";
      seriesRef.current = chart.addSeries(AreaSeries, {
        lineColor: trendColorRef.current,
        topColor: `rgba(${rgb},.28)`,
        bottomColor: `rgba(${rgb},.02)`,
        lineWidth: 2,
        ...priceLineOptions(trendColorRef.current),
      });
    }

    chartRef.current = chart;
    shouldFitRef.current = true;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [tf, chartType]);

  // 데이터 주입 — 시리즈만 갱신한다(차트 재생성 없음 → 확대/스크롤 유지).
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !data) return;
    series.setData(toSeriesData(data.bars, chartType, tf === "min"));

    // 등락 방향에 따라 선/채움 색을 시세 관례로 맞춘다(캔들은 봉마다 색이 달라 제외).
    if (chartType === "line") {
      series.applyOptions({
        color: trendColor,
        crosshairMarkerBorderColor: trendColor,
        priceLineColor: trendColor,
      } as never);
    } else if (chartType === "area") {
      const rgb = trendColor === KR_UP ? "239,68,68" : "59,130,246";
      series.applyOptions({
        lineColor: trendColor,
        topColor: `rgba(${rgb},.28)`,
        bottomColor: `rgba(${rgb},.02)`,
        crosshairMarkerBorderColor: trendColor,
        priceLineColor: trendColor,
      } as never);
    }

    if (shouldFitRef.current) {
      chartRef.current?.timeScale().fitContent();
      shouldFitRef.current = false;
    }
  }, [data, chartType, trendColor, tf]);

  const up = (data?.change ?? 0) >= 0;
  const live = isMarketOpen();

  return (
    <section className="glass relative mt-6 p-5 pt-7" data-section="stock-chart">
      <span className="file-tab absolute -top-[13px] left-5">
        <LineChart size={13} /> 시세
      </span>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-[13px] text-muted">
            {stockName ?? stockCode} 시세
            {live && !data?.is_demo && (
              <span className="inline-flex items-center gap-1 text-[11.5px] font-semibold text-[#10b981]">
                <span className="live-dot" aria-hidden="true" /> 장중 갱신
              </span>
            )}
          </div>
          <div className="mt-0.5 flex items-baseline gap-2">
            <span className="text-[28px] font-extrabold leading-none">
              {data ? data.last_price.toLocaleString("ko-KR") : "–"}
              <span className="ml-1 text-[13px] font-semibold text-muted">원</span>
            </span>
            {data && (
              <span className="text-[14px] font-bold" style={{ color: up ? KR_UP : KR_DOWN }}>
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
        <p className="mt-2 text-[11.5px] text-muted">
          출처: Yahoo Finance · 지연 시세
          {updatedAt && ` · ${updatedAt.toLocaleTimeString("ko-KR")} 갱신`}
          {!live && " · 장 마감(갱신 중지)"}
        </p>
      ) : null}
    </section>
  );
}
