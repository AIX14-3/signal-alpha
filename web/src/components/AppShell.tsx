import type { ReactNode } from "react";

type AppShellProps = {
  children: ReactNode;
};

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/">
          <span className="brand-mark" aria-hidden="true">
            Sα
          </span>
          <span>
            <p className="brand-kicker">Team LENS</p>
            <p className="brand-name">Signal Alpha</p>
          </span>
        </a>

        <nav className="nav" aria-label="주요 메뉴">
          <a className="nav-item nav-item-active" href="/">
            대시보드
          </a>
          <a className="nav-item" href="/journal">
            저널
          </a>
        </nav>
      </aside>

      <main className="main">
        <div className="content">
          <div className="notice">
            Signal Alpha는 매수·매도 추천이 아니라 데이터 소스 간 방향성을
            교차검증해 근거를 보여주는 서비스입니다.
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}
