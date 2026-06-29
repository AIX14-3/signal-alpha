'use client';

import { useEffect, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import { STORY_ITEMS } from '@/lib/signal/mock-data';
import { resolveSearchCode } from '@/lib/signal/utils';
import { useDashboard } from '../../context/DashboardContext';

interface SearchFormProps {
  autoFocus?: boolean;
}

export function SearchForm({ autoFocus }: SearchFormProps) {
  const { selectStock } = useDashboard();
  const [query, setQuery] = useState('');
  const [showAutocomplete, setShowAutocomplete] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (autoFocus) {
      const input = formRef.current?.querySelector('input');
      input?.focus();
    }
  }, [autoFocus]);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (!formRef.current?.contains(e.target as Node)) {
        setShowAutocomplete(false);
      }
    };
    document.addEventListener('click', handleClick);
    return () => document.removeEventListener('click', handleClick);
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    selectStock(resolveSearchCode(query));
    setShowAutocomplete(false);
  };

  return (
    <form ref={formRef} onSubmit={handleSubmit} className="relative mx-auto max-w-xl">
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => setShowAutocomplete(true)}
        placeholder="종목명 또는 코드로 시그널 분석 시작"
        className="w-full rounded-full border border-neutral-300 bg-white py-3.5 pl-12 pr-32 text-sm text-neutral-900 shadow-sm placeholder-neutral-400 focus:border-orange-500 focus:outline-none"
      />
      <Search className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-neutral-400" />
      <button
        type="submit"
        className="btn-orange absolute right-2 top-1/2 -translate-y-1/2 rounded-full px-5 py-2 text-xs font-bold"
      >
        시그널 수집
      </button>

      {showAutocomplete && (
        <div className="card-white absolute left-0 right-0 z-30 mt-2 overflow-hidden rounded-lg text-neutral-900 shadow-2xl">
          {STORY_ITEMS.map((s) => (
            <button
              key={s.code}
              type="button"
              onClick={() => {
                selectStock(s.code);
                setShowAutocomplete(false);
              }}
              className="flex w-full cursor-pointer justify-between px-4 py-3 hover:bg-orange-50"
            >
              <span className="font-semibold">{s.name}</span>
              <span className="text-xs text-neutral-400">{s.code}</span>
            </button>
          ))}
        </div>
      )}
    </form>
  );
}
