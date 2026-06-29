'use client';

import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
} from 'lightweight-charts';
import { CHART_COLORS, toChartTime, toLwMarkers } from '@/lib/signal/chart-utils';
import type { OhlcvBar, SignalMarker } from '@/types/chart';

interface StockChartProps {
  bars: OhlcvBar[];
  markers?: SignalMarker[];
  height?: number;
}

export function StockChart({ bars, markers = [], height = 360 }: StockChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#737373',
      },
      grid: {
        vertLines: { color: '#f5f5f5' },
        horzLines: { color: '#f5f5f5' },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#e5e5e5' },
      timeScale: { borderColor: '#e5e5e5', timeVisible: true, secondsVisible: false },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: CHART_COLORS.upColor,
      downColor: CHART_COLORS.downColor,
      borderVisible: false,
      wickUpColor: CHART_COLORS.wickUpColor,
      wickDownColor: CHART_COLORS.wickDownColor,
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: '#d4d4d4',
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });

    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const seriesMarkers = createSeriesMarkers(candleSeries, toLwMarkers(markers));

    chartRef.current = chart;
    candleRef.current = candleSeries;
    volumeRef.current = volumeSeries;
    markersRef.current = seriesMarkers;

    const resizeObserver = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    resizeObserver.observe(containerRef.current);
    chart.applyOptions({ width: containerRef.current.clientWidth });

    return () => {
      resizeObserver.disconnect();
      seriesMarkers.detach();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      markersRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    if (!candleRef.current || !volumeRef.current || bars.length === 0) return;

    candleRef.current.setData(
      bars.map((b) => ({
        time: toChartTime(b.time),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      })),
    );

    volumeRef.current.setData(
      bars.map((b) => ({
        time: toChartTime(b.time),
        value: b.volume ?? 0,
        color: b.close >= b.open ? CHART_COLORS.volumeUp : CHART_COLORS.volumeDown,
      })),
    );

    markersRef.current?.setMarkers(toLwMarkers(markers));
    chartRef.current?.timeScale().fitContent();
  }, [bars, markers]);

  if (bars.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-xl bg-neutral-50 text-sm text-neutral-400"
        style={{ height }}
      >
        차트 데이터가 없습니다.
      </div>
    );
  }

  return <div ref={containerRef} className="w-full" />;
}
