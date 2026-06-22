'use client';

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { useRouter } from 'next/navigation';
import { INITIAL_JOURNAL } from '@/lib/signal/mock-data';
import { getStockByCode } from '@/lib/signal/utils';
import type {
  AgentKey,
  AgentProgress,
  DrilldownKey,
  JournalEntry,
  QuotePeriod,
  StockData,
  StoryFilter,
} from '@/types/signal';

interface DashboardContextValue {
  selectedStock: StockData | null;
  quoteCode: string;
  quotePeriod: QuotePeriod;
  storyFilter: StoryFilter;
  activeAgentTab: AgentKey;
  showAgentPicker: boolean;
  journal: JournalEntry[];
  runLogs: string[];
  agentProgress: Record<AgentKey, AgentProgress>;
  activeDrilldown: DrilldownKey | null;
  selectStock: (code: string) => void;
  setQuoteCode: (code: string) => void;
  setQuotePeriod: (period: QuotePeriod) => void;
  setStoryFilter: (filter: StoryFilter) => void;
  setActiveAgentTab: (tab: AgentKey) => void;
  setShowAgentPicker: (show: boolean) => void;
  setActiveDrilldown: (key: DrilldownKey | null) => void;
  saveToJournal: (stock?: StockData) => void;
  toggleTrade: (id: number) => void;
  updateJournalNote: (id: number, note: string) => void;
  startAnalysis: (stock: StockData) => void;
  setRunLogs: (logs: string[] | ((prev: string[]) => string[])) => void;
  setAgentProgress: (
    progress:
      | Record<AgentKey, AgentProgress>
      | ((prev: Record<AgentKey, AgentProgress>) => Record<AgentKey, AgentProgress>),
  ) => void;
  navigateToQuote: (code: string) => void;
  reanalyze: () => void;
}

const DashboardContext = createContext<DashboardContextValue | null>(null);

export function DashboardProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [selectedStock, setSelectedStock] = useState<StockData | null>(null);
  const [quoteCode, setQuoteCodeState] = useState('000660');
  const [quotePeriod, setQuotePeriod] = useState<QuotePeriod>('1D');
  const [storyFilter, setStoryFilter] = useState<StoryFilter>('all');
  const [activeAgentTab, setActiveAgentTab] = useState<AgentKey>('dart');
  const [showAgentPicker, setShowAgentPicker] = useState(false);
  const [journal, setJournal] = useState<JournalEntry[]>(INITIAL_JOURNAL);
  const [runLogs, setRunLogs] = useState<string[]>([]);
  const [agentProgress, setAgentProgress] = useState<Record<AgentKey, AgentProgress>>({
    dart: 'idle',
    report: 'idle',
    alt: 'idle',
    debate: 'idle',
  });
  const [activeDrilldown, setActiveDrilldown] = useState<DrilldownKey | null>(null);

  const selectStock = useCallback(
    (code: string) => {
      const stock = getStockByCode(code);
      setSelectedStock(stock);
      setQuoteCodeState(code);
      setShowAgentPicker(false);
      setActiveDrilldown(null);
      setRunLogs([]);
      setAgentProgress({ dart: 'running', report: 'idle', alt: 'idle', debate: 'idle' });
      router.push('/running');
    },
    [router],
  );

  const startAnalysis = useCallback(
    (stock: StockData) => {
      setSelectedStock(stock);
      setRunLogs([]);
      setAgentProgress({ dart: 'running', report: 'idle', alt: 'idle', debate: 'idle' });
      router.push('/running');
    },
    [router],
  );

  const setQuoteCode = useCallback((code: string) => {
    setQuoteCodeState(code);
  }, []);

  const saveToJournal = useCallback((stock?: StockData) => {
    const target = stock ?? selectedStock;
    if (!target) return;
    if (journal.some((i) => i.code === target.code)) {
      alert('이미 저널에 기록되어 있습니다.');
      return;
    }
    setJournal((prev) => [
      {
        id: Date.now(),
        name: target.name,
        code: target.code,
        score: target.score,
        direction: target.direction,
        date: new Date().toISOString().split('T')[0],
        trade: false,
        note: target.summary,
      },
      ...prev,
    ]);
    alert(`${target.name} 저장 완료`);
  }, [journal, selectedStock]);

  const toggleTrade = useCallback((id: number) => {
    setJournal((prev) =>
      prev.map((i) => (i.id === id ? { ...i, trade: !i.trade } : i)),
    );
  }, []);

  const updateJournalNote = useCallback((id: number, note: string) => {
    setJournal((prev) => prev.map((i) => (i.id === id ? { ...i, note } : i)));
  }, []);

  const navigateToQuote = useCallback(
    (code: string) => {
      setQuoteCodeState(code);
      router.push(`/quote?code=${code}`);
    },
    [router],
  );

  const reanalyze = useCallback(() => {
    if (selectedStock) startAnalysis(selectedStock);
  }, [selectedStock, startAnalysis]);

  const value = useMemo(
    () => ({
      selectedStock,
      quoteCode,
      quotePeriod,
      storyFilter,
      activeAgentTab,
      showAgentPicker,
      journal,
      runLogs,
      agentProgress,
      activeDrilldown,
      selectStock,
      setQuoteCode,
      setQuotePeriod,
      setStoryFilter,
      setActiveAgentTab,
      setShowAgentPicker,
      setActiveDrilldown,
      saveToJournal,
      toggleTrade,
      updateJournalNote,
      startAnalysis,
      setRunLogs,
      setAgentProgress,
      navigateToQuote,
      reanalyze,
    }),
    [
      selectedStock,
      quoteCode,
      quotePeriod,
      storyFilter,
      activeAgentTab,
      showAgentPicker,
      journal,
      runLogs,
      agentProgress,
      activeDrilldown,
      selectStock,
      setQuoteCode,
      saveToJournal,
      toggleTrade,
      updateJournalNote,
      startAnalysis,
      navigateToQuote,
      reanalyze,
    ],
  );

  return (
    <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>
  );
}

export function useDashboard() {
  const ctx = useContext(DashboardContext);
  if (!ctx) throw new Error('useDashboard must be used within DashboardProvider');
  return ctx;
}
