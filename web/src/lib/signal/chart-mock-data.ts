import type { ChartApiResponse, ChartPeriod, OhlcvBar, SignalMarker } from '@/types/chart';
import { CHART_COLORS } from './chart-utils';
import { STOCK_DATA_TEMPLATES } from './mock-data';

function seededRandom(seed: string, index: number): number {
  let h = 0;
  const s = `${seed}:${index}`;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return (h % 1000) / 1000;
}

function getBarCount(period: ChartPeriod): number {
  const map: Record<ChartPeriod, number> = {
    '1D': 78,
    '1W': 5,
    '1M': 22,
    '6M': 126,
    YTD: 120,
    '1Y': 252,
    '5Y': 260,
  };
  return map[period];
}

function getVolatility(period: ChartPeriod): number {
  const map: Record<ChartPeriod, number> = {
    '1D': 0.003,
    '1W': 0.015,
    '1M': 0.025,
    '6M': 0.04,
    YTD: 0.05,
    '1Y': 0.08,
    '5Y': 0.15,
  };
  return map[period];
}

function parseAsOfDate(asOf: string): { y: number; m: number; d: number } {
  const datePart = asOf.split(' ')[0];
  const [y, m, d] = datePart.split('-').map(Number);
  return { y, m, d };
}

function formatBarTime(
  period: ChartPeriod,
  index: number,
  barCount: number,
  asOf: string,
): string | number {
  const { y, m, d } = parseAsOfDate(asOf);

  if (period === '1D') {
    // 09:00 KST = 00:00 UTC
    const startUtc = Date.UTC(y, m - 1, d, 0, 0, 0);
    const intervalMs = 5 * 60 * 1000;
    return Math.floor((startUtc + index * intervalMs) / 1000);
  }

  const endDate = new Date(y, m - 1, d);
  const barDate = new Date(endDate);
  const dayStep = period === '5Y' ? 7 : 1;
  barDate.setDate(barDate.getDate() - (barCount - 1 - index) * dayStep);
  return barDate.toISOString().slice(0, 10);
}

function barToMs(time: string | number): number {
  if (typeof time === 'number') return time * 1000;
  return new Date(time).getTime();
}

function findClosestBarTime(bars: OhlcvBar[], dateStr: string): string | number | null {
  const target = new Date(dateStr).getTime();
  let best: OhlcvBar | null = null;
  let bestDiff = Infinity;
  for (const b of bars) {
    const diff = Math.abs(barToMs(b.time) - target);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = b;
    }
  }
  return best?.time ?? null;
}

function buildMockMarkers(code: string, bars: OhlcvBar[]): SignalMarker[] {
  const template = STOCK_DATA_TEMPLATES[code] ?? STOCK_DATA_TEMPLATES['000660'];
  const markers: SignalMarker[] = [];

  for (const disclosure of template.dart.disclosures ?? []) {
    const barTime = findClosestBarTime(bars, disclosure.date);
    if (!barTime) continue;

    const isPositive = disclosure.impact === 'POSITIVE';
    markers.push({
      time: barTime,
      position: isPositive ? 'belowBar' : 'aboveBar',
      shape: isPositive ? 'arrowUp' : 'arrowDown',
      color: isPositive ? CHART_COLORS.markerPositive : CHART_COLORS.markerNegative,
      text: `DART: ${disclosure.title.length > 14 ? `${disclosure.title.slice(0, 14)}…` : disclosure.title}`,
      type: 'dart',
    });
  }

  for (const opinion of template.report.opinions ?? []) {
    const barTime = findClosestBarTime(bars, opinion.date);
    if (!barTime) continue;

    markers.push({
      time: barTime,
      position: 'aboveBar',
      shape: 'circle',
      color: CHART_COLORS.markerPositive,
      text: `${opinion.firm} ${opinion.rating}`,
      type: 'report',
    });
  }

  return markers;
}

export function getChartSeries(code: string, period: ChartPeriod): ChartApiResponse {
  const template = STOCK_DATA_TEMPLATES[code] ?? STOCK_DATA_TEMPLATES['000660'];
  const anchorPrice = template.quote.price;
  const barCount = getBarCount(period);
  const volatility = getVolatility(period);
  const seed = `${code}:${period}`;

  const closes: number[] = [anchorPrice];
  let price = anchorPrice;

  for (let i = barCount - 2; i >= 0; i--) {
    const delta = (seededRandom(seed, i) - 0.48) * volatility;
    price = Math.round(price / (1 + delta));
    closes.unshift(price);
  }

  const bars: OhlcvBar[] = closes.map((close, i) => {
    const wick = Math.max(1, Math.round(close * volatility * 0.5));
    const open = i === 0 ? close : closes[i - 1];
    return {
      time: formatBarTime(period, i, barCount, template.quote.asOf),
      open,
      high: Math.max(open, close) + wick,
      low: Math.min(open, close) - wick,
      close,
      volume: Math.round(seededRandom(seed, i + 1000) * 1_000_000 + 100_000),
    };
  });

  if (process.env.NODE_ENV === 'development') {
    console.assert(
      bars[bars.length - 1].close === anchorPrice,
      `[chart-mock] ${code} ${period}: last close ${bars[bars.length - 1].close} !== anchor ${anchorPrice}`,
    );
  }

  return {
    stock_code: code,
    period,
    as_of: template.quote.asOf,
    anchor_price: anchorPrice,
    bars,
    markers: buildMockMarkers(code, bars),
  };
}
