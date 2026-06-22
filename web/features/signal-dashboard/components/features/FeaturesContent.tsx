import Link from 'next/link';
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Eye,
  GitMerge,
  Scale,
  ShieldOff,
  TrendingUp,
} from 'lucide-react';
import { DIFF_CARDS, SOURCE_TABLE } from '@/lib/signal/mock-data';
import { PageShell } from '../layout/PageShell';

const iconMap = {
  'trending-up': TrendingUp,
  'git-merge': GitMerge,
  scale: Scale,
  eye: Eye,
  'book-open': BookOpen,
  'shield-off': ShieldOff,
} as const;

function TrustStars({ n }: { n: number }) {
  return (
    <>
      {Array.from({ length: 5 }, (_, i) => (
        <span key={i} className={i < n ? 'text-orange-500' : 'text-neutral-200'}>
          ★
        </span>
      ))}
    </>
  );
}

export function FeaturesContent() {
  return (
    <PageShell
      title="판단의 근거를 만드는 4가지 엔진"
      subtitle="Signal α가 무엇을·왜·어떻게 다른지"
      cta={
        <div className="mt-10 text-center">
          <Link href="/?focus=search" className="btn-orange rounded-full px-8 py-3 text-sm font-bold">
            종목 분석 시작하기 →
          </Link>
        </div>
      }
    >
      <section className="card-light mb-10 rounded-2xl p-6 shadow-sm md:p-8">
        <h2 className="mb-4 text-sm font-bold uppercase text-orange-600">멀티에이전트 파이프라인</h2>
        <div className="flex flex-wrap items-center justify-center gap-2 text-xs font-semibold text-neutral-700 md:gap-4 md:text-sm">
          <span className="rounded-full bg-neutral-100 px-3 py-2">Fan-out</span>
          <ArrowRight className="h-4 w-4 text-neutral-400" />
          <span className="rounded-full border border-orange-200 px-3 py-2 text-orange-700">DART</span>
          <span className="rounded-full border border-orange-200 px-3 py-2 text-orange-700">Report</span>
          <span className="rounded-full border border-orange-200 px-3 py-2 text-orange-700">Alt</span>
          <ArrowRight className="h-4 w-4 text-neutral-400" />
          <span className="rounded-full bg-neutral-100 px-3 py-2">Debate</span>
          <ArrowRight className="h-4 w-4 text-neutral-400" />
          <span className="card-orange rounded-full px-4 py-2 text-xs text-white">Score</span>
        </div>
        <p className="mt-4 text-center text-xs text-neutral-500">
          LangGraph Fan-out / Fan-in — 3소스 병렬 수집 후 토론·합의
        </p>
      </section>

      <section className="mb-10">
        <h2 className="mb-4 text-lg font-black">차별화 포인트</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {DIFF_CARDS.map((c) => {
            const Icon = iconMap[c.icon as keyof typeof iconMap];
            return (
              <div key={c.title} className="card-light rounded-xl p-5 shadow-sm">
                <Icon className="mb-3 h-6 w-6 text-orange-500" />
                <h3 className="font-bold text-neutral-900">{c.title}</h3>
                <p className="mt-2 text-xs leading-relaxed text-neutral-600">{c.desc}</p>
              </div>
            );
          })}
        </div>
      </section>

      <section className="card-light mb-10 overflow-x-auto rounded-2xl p-6 shadow-sm">
        <h2 className="mb-4 text-lg font-black">데이터 소스 신뢰도</h2>
        <table className="w-full min-w-[480px] text-left text-sm">
          <thead>
            <tr className="text-[10px] uppercase text-neutral-500">
              <th className="pb-2">소스</th>
              <th>유형</th>
              <th>수집</th>
              <th>비용</th>
              <th>신뢰도</th>
            </tr>
          </thead>
          <tbody>
            {SOURCE_TABLE.map((r) => (
              <tr key={r.name} className="border-t border-neutral-100">
                <td className="py-3 font-medium">{r.name}</td>
                <td className="py-3 text-xs text-neutral-500">{r.type}</td>
                <td className="py-3 text-xs">{r.method}</td>
                <td className="py-3 text-xs">{r.cost}</td>
                <td className="py-3">
                  <TrustStars n={r.trust} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="mb-10 grid gap-6 md:grid-cols-2">
        <div className="card-light rounded-xl border-amber-200 bg-amber-50 p-5 shadow-sm">
          <h3 className="flex items-center gap-2 font-bold text-amber-800">
            <AlertTriangle className="h-5 w-5" />
            Human-in-the-Loop
          </h3>
          <p className="mt-2 text-xs text-neutral-600">
            confidence &lt; 60 → 추가 확인 배너 (예: 삼성전자 58점)
          </p>
          <div className="mt-4 rounded-lg border border-amber-200 bg-white p-3 text-sm text-neutral-700">
            신뢰 58점 — <span className="font-semibold text-orange-600">원본 실사</span> 권장
          </div>
        </div>
        <div className="card-light rounded-xl p-5 shadow-sm">
          <h3 className="font-bold text-neutral-900">규제 · 정책</h3>
          <ul className="mt-3 list-disc space-y-2 pl-4 text-xs text-neutral-600">
            <li>투자자문업 회피 — 매수/매도 권유 없음</li>
            <li>리포트 PDF 원문 미노출 (요약·메타만)</li>
            <li>Known Issue: 크롤링 지연·루머 노이즈</li>
          </ul>
        </div>
      </section>

      <blockquote className="border-l-4 border-orange-500 pl-4 text-sm italic text-neutral-600">
        &quot;매수하세요&quot;가 아니라 &quot;여러 소스가 같은 방향을 가리키고 있습니다&quot;
      </blockquote>
    </PageShell>
  );
}
