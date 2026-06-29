import type { ChartApiResponse, ChartPeriod } from '@/types/chart';
import { getChartSeries } from './chart-mock-data';

/**
 * Phase 4: GET /api/chart/{stock_code}?period={period}&markers=true
 * 현재는 목업 데이터를 ChartApiResponse 형태로 반환한다.
 */
export async function fetchChartSeries(
  code: string,
  period: ChartPeriod,
  _options?: { markers?: boolean },
): Promise<ChartApiResponse> {
  return getChartSeries(code, period);
}
