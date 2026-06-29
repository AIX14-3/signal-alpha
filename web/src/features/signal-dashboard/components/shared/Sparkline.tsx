interface SparklineProps {
  values: number[];
  up: boolean;
}

export function Sparkline({ values, up }: SparklineProps) {
  const max = Math.max(...values, 1);
  const min = Math.min(...values);
  const range = max - min || 1;
  const pts = values
    .map((v, i) => `${(i / (values.length - 1)) * 100},${100 - ((v - min) / range) * 80}`)
    .join(' ');
  const stroke = up ? '#22c55e' : '#ef4444';

  return (
    <svg viewBox="0 0 100 100" className="h-8 w-14" preserveAspectRatio="none">
      <polyline fill="none" stroke={stroke} strokeWidth="3" points={pts} />
    </svg>
  );
}
