import { agreementLabel, directionLabel, scoreOutOf10 } from "@/lib/format";

const TONE_CLASS: Record<string, string> = {
  up: "text-green",
  down: "text-red",
  flat: "text-muted",
};

/** v61 AI Score 블롭 + 게이지. score는 0–100, 표기는 /10. */
export function ScoreGauge({
  score,
  direction,
  agreement,
}: {
  score: number | null;
  direction: string | null;
  agreement: string | null;
}) {
  const pct = Math.max(0, Math.min(100, score ?? 0));
  const radius = 74;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - pct / 100);
  const verdict = directionLabel(direction);

  return (
    <div className="card flex flex-col items-center justify-center p-7 text-center">
      <div className="relative h-[170px] w-[170px]">
        <svg width="170" height="170" className="-rotate-90">
          <circle cx="85" cy="85" r={radius} fill="none" stroke="#F3F6FB" strokeWidth="14" />
          <circle
            cx="85"
            cy="85"
            r={radius}
            fill="none"
            stroke="url(#gaugeGrad)"
            strokeWidth="14"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
          />
          <defs>
            <linearGradient id="gaugeGrad" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="#0EA5E9" />
              <stop offset="1" stopColor="#10B981" />
            </linearGradient>
          </defs>
        </svg>
        <div className="absolute inset-0 grid place-items-center">
          <div>
            <b className="text-[46px] font-extrabold leading-none tracking-[-0.03em]">
              {scoreOutOf10(score)}
            </b>
            <span className="block text-[12px] text-muted">/ 10 AI Score</span>
          </div>
        </div>
      </div>
      <span className={`mt-2 text-[15px] font-bold ${TONE_CLASS[verdict.tone]}`}>
        {verdict.label}
      </span>
      <div className="mt-3 text-[12px] text-muted">
        신뢰도 <b className="text-navy">{agreementLabel(agreement)}</b>
      </div>
    </div>
  );
}
