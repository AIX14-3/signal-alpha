'use client';

import Link from 'next/link';
import { useEffect, useRef } from 'react';
import { useAnalysisSimulation } from '../../hooks/useAnalysisSimulation';
import { useDashboard } from '../../context/DashboardContext';
import { AgentCircle } from '../shared/AgentCircle';

export function RunningContent() {
  useAnalysisSimulation();
  const { selectedStock, runLogs, agentProgress } = useDashboard();
  const endRef = useRef<HTMLDivElement>(null);
  const done = agentProgress.debate === 'completed';

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [runLogs]);

  if (!selectedStock) return null;

  return (
    <div className="animate-fade-in mx-auto max-w-5xl px-4 py-12">
      <div className="mb-8 flex items-center justify-between border-b border-neutral-200 pb-6">
        <div>
          <h2 className="text-2xl font-black text-neutral-900">{selectedStock.name}</h2>
          <p className="mt-1 font-mono text-xs text-neutral-500">에이전트 연산 중 · SSE</p>
        </div>
        <span
          className={`rounded-full px-4 py-2 text-xs font-bold ${
            done ? 'card-orange' : 'card-light text-orange-600'
          }`}
        >
          {done ? '완료' : '수집 중'}
        </span>
      </div>

      <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-4">
        <AgentCircle agentKey="dart" progress={agentProgress.dart} />
        <AgentCircle agentKey="report" progress={agentProgress.report} />
        <AgentCircle agentKey="alt" progress={agentProgress.alt} />
        <AgentCircle agentKey="debate" progress={agentProgress.debate} />
      </div>

      <div className="card-light mb-8 max-h-80 overflow-y-auto rounded-xl bg-neutral-50 p-5 font-mono">
        {runLogs.map((log, i) => (
          <div key={i} className="py-1 text-xs text-neutral-700">
            <span className="text-neutral-400">[{new Date().toLocaleTimeString()}]</span> {log}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      {done ? (
        <div className="flex flex-wrap gap-3">
          <Link href="/dashboard/main" className="btn-orange rounded-full px-8 py-3 text-sm font-bold">
            시그널 분석 보기 →
          </Link>
          <Link
            href={`/dashboard/quote?code=${selectedStock.code}`}
            className="btn-outline rounded-full px-8 py-3 text-sm font-bold"
          >
            종목 시세 보기 →
          </Link>
        </div>
      ) : (
        <p className="animate-pulse text-xs text-neutral-500">합의 대기 중...</p>
      )}
    </div>
  );
}
