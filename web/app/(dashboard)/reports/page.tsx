import { Suspense } from 'react';
import { BrokerReportContent } from '@/features/signal-dashboard/components/broker/BrokerReportContent';

export default function ReportsPage() {
  return (
    <Suspense fallback={<div className="py-20 text-center text-neutral-500">로딩 중...</div>}>
      <BrokerReportContent />
    </Suspense>
  );
}
