import { Suspense } from 'react';
import { StoriesContent } from '@/features/signal-dashboard/components/stories/StoriesContent';

export default function StoriesPage() {
  return (
    <Suspense fallback={<div className="py-20 text-center text-neutral-500">로딩 중...</div>}>
      <StoriesContent />
    </Suspense>
  );
}
