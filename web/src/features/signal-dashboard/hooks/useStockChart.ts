'use client';

import { useMemo } from 'react';
import { getChartSeries } from '@/lib/signal/chart-mock-data';
import type { ChartPeriod } from '@/types/chart';

export function useStockChart(code: string, period: ChartPeriod) {
  const data = useMemo(() => getChartSeries(code, period), [code, period]);

  return {
    bars: data.bars,
    markers: data.markers ?? [],
    anchorPrice: data.anchor_price,
    asOf: data.as_of,
    period: data.period,
  };
}
