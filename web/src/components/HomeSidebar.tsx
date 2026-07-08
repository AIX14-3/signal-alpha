import Link from "next/link";

// 홈 대시보드 좌측 사이드바(레퍼런스 오마주). 요금제 폐지 방침에 따라 Upgrade 카드 대신
// 중립 정보 카드. 홈 전용이라 대시보드 활성은 고정. lg 이상에서만 노출(모바일은 상단 메뉴 사용).
const NAV: { href: string; label: string; icon: string; active?: boolean }[] = [
  { href: "/", label: "대시보드", icon: "▦", active: true },
  { href: "/community", label: "커뮤니티", icon: "💬" },
  { href: "/postmortem", label: "매매 부검", icon: "🧬" },
  { href: "/methodology", label: "방법론", icon: "📐" },
  { href: "/mypage", label: "마이", icon: "👤" },
];

export function HomeSidebar() {
  return (
    <aside className="hidden lg:flex lg:flex-col lg:gap-1">
      <div className="px-2 pb-2 text-[11px] font-bold uppercase tracking-wider text-muted">메뉴</div>
      <nav className="flex flex-col gap-1">
        {NAV.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`flex items-center gap-2.5 rounded-[12px] px-3 py-2.5 text-[13.5px] font-semibold transition ${
              item.active
                ? "brand-grad text-white shadow-[var(--shadow-card)]"
                : "text-navy-soft hover:bg-surface-2"
            }`}
          >
            <span className="w-4 text-center text-[14px]">{item.icon}</span>
            {item.label}
          </Link>
        ))}
      </nav>

      <div className="glass-card mt-auto p-3">
        <div className="flex items-center gap-2 text-[12px] font-bold text-navy">
          <span className="live-dot" /> 실시간 분석 중
        </div>
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-muted">
          멀티에이전트가 공시·재무·뉴스·수급·시계열을 상시 분석합니다.
        </p>
      </div>
    </aside>
  );
}
