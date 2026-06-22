export type ChartPeriod = '1D' | '1W' | '1M' | '6M' | 'YTD' | '1Y' | '5Y';

/** lightweight-charts CandlestickData 호환 */
export interface OhlcvBar {
  time: string | number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface ChartSeries {
  period: ChartPeriod;
  bars: OhlcvBar[];
}

export interface SignalMarker {
  time: string | number;
  position: 'aboveBar' | 'belowBar';
  color: string;
  shape: 'circle' | 'arrowUp' | 'arrowDown';
  text: string;
  type?: 'dart' | 'report' | 'alt';
}

export interface ChartApiResponse {
  stock_code: string;
  period: ChartPeriod;
  as_of: string;
  anchor_price: number;
  bars: OhlcvBar[];
  markers?: SignalMarker[];
  backtest?: BacktestSummary;
}

export interface BacktestSummary {
  signal_count: number;
  hit_rate: number;
  avg_return_pct: number;
  signals: BacktestSignal[];
}

export interface BacktestSignal {
  time: string;
  type: 'dart' | 'report' | 'alt';
  title: string;
  price_at_signal: number;
  price_after_7d: number;
  return_pct: number;
  hit: boolean;
}
