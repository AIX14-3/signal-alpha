interface PriceChartProps {
  points: number[];
}

export function PriceChart({ points }: PriceChartProps) {
  const w = 600;
  const h = 200;
  const pad = 12;
  const min = Math.min(...points) * 0.998;
  const max = Math.max(...points) * 1.002;
  const range = max - min || 1;

  const coords = points.map((p, i) => {
    const x = pad + (i / (points.length - 1)) * (w - pad * 2);
    const y = h - pad - ((p - min) / range) * (h - pad * 2);
    return [x, y] as const;
  });

  const line = coords.map((c) => c.join(',')).join(' ');
  const area = `${coords[0][0]},${h} ${line} ${coords[coords.length - 1][0]},${h}`;

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-48 w-full">
      <defs>
        <linearGradient id="quoteGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#3b82f6" />
          <stop offset="100%" stopColor="#3b82f6" stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon className="quote-chart-area" points={area} />
      <polyline className="quote-chart-line" points={line} />
    </svg>
  );
}
