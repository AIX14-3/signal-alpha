'use client';

import { useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import type { AgentKey, AgentProgress, StockData } from '@/types/signal';
import { useDashboard } from '../context/DashboardContext';

const LOGS = (target: StockData) => [
  { agent: 'dart' as const, text: 'DART Watcher: 공시 수집 시작...', delay: 500 },
  { agent: 'dart' as const, text: `DART: ${target.dart.score}점 완료`, status: 'done' as const, delay: 2800 },
  { agent: 'report' as const, text: 'Report RAG: 리포트 파싱...', delay: 3200 },
  { agent: 'report' as const, text: `Report: ${target.report.score}점`, status: 'done' as const, delay: 5800 },
  { agent: 'alt' as const, text: 'Alternative: 채용·특허 분석...', delay: 6200 },
  { agent: 'alt' as const, text: `Alt: ${target.alt.score}점`, status: 'done' as const, delay: 8400 },
  { agent: 'debate' as const, text: 'Debate: Bull vs Bear...', delay: 8900 },
  {
    agent: 'debate' as const,
    text: `최종 ${target.score}점 [${target.direction}]`,
    status: 'done' as const,
    delay: 10500,
  },
];

export function useAnalysisSimulation() {
  const router = useRouter();
  const { selectedStock, setRunLogs, setAgentProgress } = useDashboard();
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    if (!selectedStock) {
      router.replace('/');
      return;
    }

    timersRef.current.forEach(clearTimeout);
    timersRef.current = [];
    setRunLogs([]);
    setAgentProgress({ dart: 'running', report: 'idle', alt: 'idle', debate: 'idle' });

    const logs = LOGS(selectedStock);
    logs.forEach((log) => {
      const timer = setTimeout(() => {
        setRunLogs((prev) => [...prev, log.text]);
        if (log.status === 'done') {
          setAgentProgress((prev) => {
            const next = { ...prev };
            if (log.agent === 'dart') {
              next.dart = 'completed';
              next.report = 'running';
            } else if (log.agent === 'report') {
              next.report = 'completed';
              next.alt = 'running';
            } else if (log.agent === 'alt') {
              next.alt = 'completed';
              next.debate = 'running';
            } else if (log.agent === 'debate') {
              next.debate = 'completed';
            }
            return next;
          });
        }
      }, log.delay);
      timersRef.current.push(timer);
    });

    return () => {
      timersRef.current.forEach(clearTimeout);
      timersRef.current = [];
    };
  }, [selectedStock, router, setRunLogs, setAgentProgress]);
}
