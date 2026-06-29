import type { ChartPeriod } from '@/types/chart';

const PERIODS: ChartPeriod[] = ['1D', '1W', '1M', '6M', 'YTD', '1Y', '5Y'];

interface ChartPeriodTabsProps {
  period: ChartPeriod;
  onChange: (period: ChartPeriod) => void;
}

export function ChartPeriodTabs({ period, onChange }: ChartPeriodTabsProps) {
  return (
    <div className="flex flex-wrap gap-1">
      {PERIODS.map((p) => (
        <button
          key={p}
          type="button"
          onClick={() => onChange(p)}
          className={`rounded-lg border px-3 py-1 text-xs ${
            period === p ? 'period-tab-active' : 'border-neutral-200 text-neutral-500'
          }`}
        >
          {p}
        </button>
      ))}
    </div>
  );
}
