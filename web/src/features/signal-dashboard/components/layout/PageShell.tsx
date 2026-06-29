import type { ReactNode } from 'react';

interface PageShellProps {
  title: string;
  subtitle?: string;
  children: ReactNode;
  cta?: ReactNode;
}

export function PageShell({ title, subtitle, children, cta }: PageShellProps) {
  return (
    <div className="animate-fade-in mx-auto max-w-7xl px-4 py-10 md:px-8">
      <div className="mb-10">
        <h1 className="text-2xl font-black text-neutral-900 md:text-3xl">{title}</h1>
        {subtitle && <p className="mt-2 text-sm text-neutral-500">{subtitle}</p>}
      </div>
      {children}
      {cta}
    </div>
  );
}
