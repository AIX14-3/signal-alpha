'use client';

import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { DashboardProvider } from '../../context/DashboardContext';
import { DrilldownDrawer } from '../drilldown/DrilldownDrawer';
import { Footer } from './Footer';
import { Header } from './Header';
import { ScrollToTop } from './ScrollToTop';

function DashboardLayoutInner({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isHome = pathname === '/dashboard';

  return (
    <div className={`flex min-h-screen flex-col ${isHome ? 'bg-white' : 'bg-neutral-50'}`}>
      <Header />
      <main className={`flex-1 ${isHome ? 'p-0' : 'py-6'}`}>{children}</main>
      <Footer />
      <DrilldownDrawer />
      <ScrollToTop />
    </div>
  );
}

export function DashboardShell({ children }: { children: ReactNode }) {
  return (
    <DashboardProvider>
      <DashboardLayoutInner>{children}</DashboardLayoutInner>
    </DashboardProvider>
  );
}
