import { CHART_COLORS } from '@/lib/signal/chart-utils';

export function ChartLegend({ markerCount }: { markerCount: number }) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-neutral-500">
      <span className="flex items-center gap-1">
        <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: CHART_COLORS.upColor }} />
        상승
      </span>
      <span className="flex items-center gap-1">
        <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: CHART_COLORS.downColor }} />
        하락
      </span>
      {markerCount > 0 && (
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{ background: CHART_COLORS.markerPositive }}
          />
          시그널 {markerCount}건
        </span>
      )}
    </div>
  );
}
