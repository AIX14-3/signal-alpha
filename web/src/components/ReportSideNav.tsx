"use client";

import {
  Clock,
  Compass,
  FileText,
  LayoutGrid,
  LineChart,
  type LucideProps,
  PenLine,
  Pin,
  Search,
  Target,
} from "lucide-react";
import type { ComponentType } from "react";

// 리포트 전용 좌측 미니 네비 레일 — 상단 글로벌 네비는 유지하고, 세부 섹션으로 점프하는
// 아이콘+라벨 탭을 글래스 패널 컬럼에 담는다(레퍼런스 좌측 레일 스타일).
const SECTIONS: { id: string; Icon: ComponentType<LucideProps>; label: string }[] = [
  { id: "sec-summary", Icon: Target, label: "종합" },
  { id: "sec-chart", Icon: LineChart, label: "시세" },
  { id: "sec-sources", Icon: LayoutGrid, label: "소스별" },
  { id: "sec-agreement", Icon: Compass, label: "일치도" },
  { id: "sec-evidence", Icon: Pin, label: "근거" },
  { id: "sec-trace", Icon: Search, label: "흔적" },
  { id: "sec-timeline", Icon: Clock, label: "이력" },
  { id: "sec-precedent", Icon: FileText, label: "사례" },
  { id: "sec-journal", Icon: PenLine, label: "저널" },
];

export function ReportSideNav() {
  return (
    <aside
      className="sticky top-1/2 hidden h-fit shrink-0 -translate-y-1/2 lg:block"
      data-section="report-rail"
    >
      <div className="glass flex flex-col items-center gap-1 p-2">
        {SECTIONS.map(({ id, Icon, label }) => (
          <a
            key={id}
            href={`#${id}`}
            title={label}
            aria-label={label}
            className="group flex w-[52px] flex-col items-center gap-1 rounded-[12px] py-2 transition hover:bg-white/70"
          >
            <Icon size={18} className="text-navy-soft group-hover:text-navy" />
            <span className="text-[10px] font-semibold text-muted group-hover:text-navy">
              {label}
            </span>
          </a>
        ))}
      </div>
    </aside>
  );
}
