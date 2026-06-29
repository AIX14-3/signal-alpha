export function StockChartSkeleton({ height = 360 }: { height?: number }) {
  return (
    <div
      className="animate-pulse rounded-xl bg-neutral-100"
      style={{ height }}
      aria-label="차트 로딩 중"
    >
      <div className="flex h-full flex-col justify-end gap-1 px-4 pb-8 pt-4">
        {Array.from({ length: 12 }).map((_, i) => (
          <div
            key={i}
            className="h-2 rounded bg-neutral-200"
            style={{ width: `${40 + (i % 5) * 12}%`, marginLeft: `${(i % 3) * 8}%` }}
          />
        ))}
      </div>
    </div>
  );
}
