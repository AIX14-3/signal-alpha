'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Search } from 'lucide-react';
import { NAV_ITEMS } from '@/lib/signal/mock-data';
import { BrandLogo } from './BrandLogo';

export function Header() {
  const pathname = usePathname();
  const inFlow = pathname === '/running' || pathname === '/main';

  return (
    <header className="header-solid sticky top-0 z-40">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-4 md:px-8">
        <Link href="/" className="shrink-0 cursor-pointer">
          <BrandLogo />
        </Link>

        <nav className="hidden flex-1 items-center justify-center gap-1 lg:flex">
          {NAV_ITEMS.map((item, i) => {
            const active = pathname === item.href;
            return (
              <span key={item.href} className="flex items-center">
                {i > 0 && <span className="mx-1 hidden text-neutral-300 md:inline">·</span>}
                <Link
                  href={item.href}
                  className={`text-sm transition-colors ${
                    active
                      ? 'nav-active'
                      : 'text-neutral-600 hover:text-orange-600'
                  }`}
                >
                  {item.label}
                </Link>
              </span>
            );
          })}
        </nav>

        <div className="flex shrink-0 items-center gap-3 text-sm">
          {inFlow && (
            <span className="hidden rounded-full bg-orange-50 px-2 py-1 text-[10px] font-bold text-orange-600 sm:inline">
              분석 중
            </span>
          )}
          <Link
            href="/?focus=search"
            className="hidden text-neutral-500 hover:text-orange-600 sm:inline"
          >
            <Search className="h-4 w-4" />
          </Link>
          <Link
            href="/?focus=search"
            className="btn-orange whitespace-nowrap rounded-full px-4 py-2 text-xs font-bold"
          >
            무료 시작
          </Link>
        </div>
      </div>

      <nav className="flex flex-wrap justify-center gap-x-2 gap-y-1 border-t border-neutral-100 px-4 pb-3 text-xs lg:hidden">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`px-2 py-1 ${active ? 'font-bold text-orange-600' : 'text-neutral-600'}`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
