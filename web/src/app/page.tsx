import Link from "next/link";
import { SearchHero } from "@/components/SearchHero";

const SAMPLES = [
  { code: "005930", name: "삼성전자" },
  { code: "000660", name: "SK하이닉스" },
  { code: "035420", name: "NAVER" },
];

export default function HomePage() {
  return (
    <div className="py-10">
      <SearchHero />

      <section className="mt-12">
        <p className="mb-4 text-[13px] font-bold text-muted">인기 종목 바로 보기</p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {SAMPLES.map((stock) => (
            <Link
              key={stock.code}
              href={`/report/${stock.code}`}
              className="card flex items-center justify-between px-5 py-4 hover:border-sky"
            >
              <span className="font-bold">{stock.name}</span>
              <span className="text-[13px] text-muted">{stock.code}</span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
