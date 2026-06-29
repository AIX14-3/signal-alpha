'use client';

import {
  Activity,
  Database,
  FileText,
  Scale,
} from 'lucide-react';
import { AGENT_HUB, STORY_ITEMS } from '@/lib/signal/mock-data';
import { dirChipClass } from '@/lib/signal/utils';
import type { AgentKey } from '@/types/signal';
import { useDashboard } from '../../context/DashboardContext';
import { PageShell } from '../layout/PageShell';

const iconMap = {
  'file-text': FileText,
  database: Database,
  activity: Activity,
  scale: Scale,
} as const;

function AgentPanel({ tab }: { tab: AgentKey }) {
  const a = AGENT_HUB[tab];
  const Icon = iconMap[a.icon as keyof typeof iconMap];
  const { setShowAgentPicker } = useDashboard();

  return (
    <div className="card-light rounded-2xl p-6 shadow-sm">
      <p className="text-xs text-neutral-500">담당 · {a.owner}</p>
      <h3 className="mt-1 flex items-center gap-2 text-xl font-black">
        <Icon className="h-5 w-5 text-orange-500" />
        {a.label}
      </h3>
      <p className="mt-3 text-sm text-neutral-700">{a.role}</p>
      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <div>
          <h4 className="mb-2 text-[10px] font-bold uppercase text-neutral-500">수집 방법</h4>
          <ul>
            {a.methods.map((m) => (
              <li key={m} className="text-xs text-neutral-600">
                · {m}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h4 className="mb-2 text-[10px] font-bold uppercase text-neutral-500">처리 로직</h4>
          <ul>
            {a.logic.map((l) => (
              <li key={l} className="text-xs text-neutral-600">
                · {l}
              </li>
            ))}
          </ul>
        </div>
      </div>
      <h4 className="mb-2 mt-6 text-[10px] font-bold uppercase text-neutral-500">출력 샘플</h4>
      <pre className="json-sample text-neutral-800">{a.json}</pre>
      <div className="mt-4 flex flex-wrap gap-3">
        <span className="card-orange rounded-lg px-4 py-2 text-sm font-bold">{a.mock.score}점</span>
        <span className={`rounded-full border px-3 py-2 text-xs ${dirChipClass('POSITIVE')}`}>
          {a.mock.signal}
        </span>
        <span className="text-xs text-neutral-500">{a.mock.field}</span>
      </div>
      <button
        type="button"
        onClick={() => setShowAgentPicker(true)}
        className="btn-orange mt-6 rounded-full px-5 py-2.5 text-xs font-bold"
      >
        이 에이전트로 분석하기
      </button>
    </div>
  );
}

export function AgentsContent() {
  const { activeAgentTab, setActiveAgentTab, showAgentPicker, setShowAgentPicker, selectStock } =
    useDashboard();

  return (
    <PageShell
      title="4 Agents, 하나의 신뢰 점수"
      subtitle="분석 전 에이전트별 역할·입출력 학습 허브"
    >
      <div className="grid gap-8 lg:grid-cols-12">
        <div className="lg:col-span-4">
          {(Object.keys(AGENT_HUB) as AgentKey[]).map((k) => {
            const a = AGENT_HUB[k];
            const Icon = iconMap[a.icon as keyof typeof iconMap];
            const active = activeAgentTab === k;
            return (
              <button
                key={k}
                type="button"
                onClick={() => setActiveAgentTab(k)}
                className={`mb-2 w-full rounded-xl border px-4 py-3 text-left text-sm transition-colors ${
                  active ? 'agent-tab-active' : 'card-light hover:border-orange-200'
                }`}
              >
                <Icon className="mr-2 inline h-4 w-4" />
                {a.label}
              </button>
            );
          })}
        </div>
        <div className="lg:col-span-8">
          <AgentPanel tab={activeAgentTab} />
        </div>
      </div>

      <section className="card-light mt-10 rounded-xl p-6 shadow-sm">
        <h3 className="font-bold text-neutral-900">스코어링 공식</h3>
        <p className="mt-2 text-sm text-neutral-600">
          DART <strong>35%</strong> + Report <strong>40%</strong> + Alternative <strong>25%</strong>
        </p>
        <p className="mt-2 text-xs text-neutral-500">
          보정: confidence &lt; 60 → HITL 배너 · 소스 충돌 시 −10점
        </p>
      </section>

      {showAgentPicker && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <button
            type="button"
            aria-label="닫기"
            onClick={() => setShowAgentPicker(false)}
            className="absolute inset-0 bg-neutral-900/30"
          />
          <div className="card-white relative w-full max-w-sm rounded-2xl p-6 shadow-2xl">
            <h4 className="mb-4 font-bold">종목 선택</h4>
            <div className="space-y-2">
              {STORY_ITEMS.map((s) => (
                <button
                  key={s.code}
                  type="button"
                  onClick={() => selectStock(s.code)}
                  className="w-full rounded-lg border border-neutral-200 px-4 py-3 text-left font-semibold hover:border-orange-400"
                >
                  {s.name} <span className="text-xs text-neutral-400">{s.code}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </PageShell>
  );
}
