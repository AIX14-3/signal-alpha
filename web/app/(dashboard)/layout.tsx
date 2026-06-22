import { DashboardShell } from '@/features/signal-dashboard/components/layout/DashboardShell';

export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return <DashboardShell>{children}</DashboardShell>;
}
