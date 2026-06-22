import { Suspense } from 'react';
import { QuoteContent } from '@/features/signal-dashboard/components/quote/QuoteContent';

export default function QuotePage() {
  return (
    <Suspense fallback={<div className="py-20 text-center text-neutral-500">로딩 중...</div>}>
      <QuoteContent />
    </Suspense>
  );
}
