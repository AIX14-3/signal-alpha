import type { ChartPeriod, OhlcvBar, SignalMarker } from '@/types/chart';
import type { SeriesMarker, Time, UTCTimestamp } from 'lightweight-charts';

/** 한국 시장 기준 — 상승 빨강 / 하락 파랑 */
export const CHART_COLORS = {
  upColor: '#ef4444',
  downColor: '#2563eb',
  wickUpColor: '#ef4444',
  wickDownColor: '#2563eb',
  volumeUp: '#fecaca',
  volumeDown: '#bfdbfe',
  markerPositive: '#ea580c',
  markerNegative: '#9333ea',
} as const;

export function toChartTime(time: string | number): Time {
  if (typeof time === 'number') return time as UTCTimestamp;
  return time;
}

export function toLwMarkers(markers: SignalMarker[]): SeriesMarker<Time>[] {
  return markers.map((m) => ({
    time: toChartTime(m.time),
    position: m.position,
    shape: m.shape,
    color: m.color,
    text: m.text,
  }));
}

export function findBarTimeForDate(bars: OhlcvBar[], dateStr: string): string | number | null {
  const target = dateStr.slice(0, 10);
  const match = bars.find((b) => {
    if (typeof b.time === 'string') return b.time === target;
    const d = new Date((b.time as number) * 1000);
    return d.toISOString().slice(0, 10) === target;
  });
  return match?.time ?? bars[Math.floor(bars.length / 2)]?.time ?? null;
}

export function getChartTimeAxisLabel(period: ChartPeriod): { start: string; mid: string; end: string } {
  if (period === '1D') {
    return { start: '09:00', mid: '12:00', end: '15:30' };
  }
  if (period === '1W') return { start: '5일 전', mid: '3일 전', end: '오늘' };
  if (period === '1M') return { start: '1개월 전', mid: '2주 전', end: '오늘' };
  if (period === '6M') return { start: '6개월 전', mid: '3개월 전', end: '오늘' };
  if (period === 'YTD') return { start: '연초', mid: '중반', end: '오늘' };
  if (period === '1Y') return { start: '1년 전', mid: '6개월 전', end: '오늘' };
  return { start: '5년 전', mid: '2년 전', end: '오늘' };
}
