import { Landmark, Lightbulb, LineChart, type LucideProps, ScrollText, Search, Users } from "lucide-react";
import type { ComponentType } from "react";

// 소스 키 → lucide 라인 아이콘(이모지 대체).
const MAP: Record<string, ComponentType<LucideProps>> = {
  price: LineChart,
  dart: ScrollText,
  hiring: Users,
  datalab: Search,
  patent: Lightbulb,
  report: Landmark,
};

export function SourceIcon({
  source,
  size = 16,
  className,
}: {
  source: string | null | undefined;
  size?: number;
  className?: string;
}) {
  const Icon = MAP[source ?? ""] ?? LineChart;
  return <Icon size={size} className={className} aria-hidden="true" />;
}
