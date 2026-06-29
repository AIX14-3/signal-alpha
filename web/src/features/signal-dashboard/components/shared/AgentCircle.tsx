import { Check } from 'lucide-react';
import type { AgentKey, AgentProgress } from '@/types/signal';

const LABELS: Record<AgentKey, { label: string; sub: string; code: string }> = {
  dart: { label: 'DART', sub: '공시', code: 'A1' },
  report: { label: 'Report', sub: 'RAG', code: 'A2' },
  alt: { label: 'Alt', sub: '대체데이터', code: 'A3' },
  debate: { label: 'Debate', sub: '토론', code: 'A4' },
};

interface AgentCircleProps {
  agentKey: AgentKey;
  progress: AgentProgress;
}

export function AgentCircle({ agentKey, progress }: AgentCircleProps) {
  const { label, sub, code } = LABELS[agentKey];
  const done = progress === 'completed';
  const running = progress === 'running';

  return (
    <div
      className={`rounded-xl border p-5 text-center ${
        done
          ? 'card-orange border-transparent'
          : running
            ? 'card-light border-orange-400'
            : 'card-light border-neutral-200'
      }`}
    >
      <div
        className={`mx-auto mb-2 flex h-11 w-11 items-center justify-center rounded-full border-2 text-sm font-bold ${
          done
            ? 'border-white bg-white/20 text-white'
            : running
              ? 'animate-pulse border-orange-500 text-orange-600'
              : 'border-neutral-300 text-neutral-500'
        }`}
      >
        {done ? <Check className="h-5 w-5" /> : code}
      </div>
      <span className="block text-xs font-bold text-neutral-800">{label}</span>
      <span className="text-[10px] text-neutral-500">{sub}</span>
    </div>
  );
}
