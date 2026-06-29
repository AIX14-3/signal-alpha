import type { Direction, JournalEntry, StockData } from '@/types/signal';
import { STOCK_DATA_TEMPLATES } from './mock-data';

export function fmtWon(n: number): string {
  return Number(n).toLocaleString('ko-KR');
}

export function dirChipClass(d: Direction | string | null | undefined): string {
  if (d === 'POSITIVE') return 'bg-orange-100 border-orange-200 text-orange-700';
  if (d === 'NEGATIVE') return 'bg-red-50 border-red-200 text-red-600';
  return 'bg-amber-50 border-amber-200 text-amber-700';
}

export function dirTextClass(d: Direction | string | null | undefined): string {
  if (d === 'POSITIVE') return 'text-orange-600';
  if (d === 'NEGATIVE') return 'text-red-600';
  return 'text-amber-600';
}

export function getStockByCode(code: string): StockData {
  const template = STOCK_DATA_TEMPLATES[code] ?? STOCK_DATA_TEMPLATES['000660'];
  return structuredClone(template);
}

export function resolveSearchCode(query: string): string {
  const q = query.trim();
  if (!q) return '000660';
  if (q.includes('삼성') || q.includes('005930')) return '005930';
  if (q.includes('네이버') || q.toLowerCase().includes('naver') || q.includes('035420')) return '035420';
  return '000660';
}

export function getPatternRatio(journal: JournalEntry[]): number {
  const positive = journal.filter(
    (j) => (j.direction === 'POSITIVE' || j.score >= 70) && j.trade,
  ).length;
  const total = journal.filter((j) => j.direction === 'POSITIVE' || j.score >= 70).length;
  return total > 0 ? Math.round((positive / total) * 100) : 0;
}

export function surprisePct(stock: StockData): number {
  const { actual, consensus } = stock.dart.surprise;
  return Math.round(((actual - consensus) / consensus) * 100);
}
