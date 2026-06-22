import type { ReportOpinion } from '@/types/signal';

export interface BrokerReportHighlight {
  id: string;
  firm: string;
  title: string;
  analyst: string;
  date: string;
  rating: string;
  target: string;
  excerpt: string;
}

export const EXTENDED_OPINIONS: Record<string, ReportOpinion[]> = {
  '000660': [
    { firm: '메리츠증권', rating: 'BUY', target: '2,550,000', date: '2026-06-01' },
    { firm: 'KB증권', rating: 'BUY', target: '2,620,000', date: '2026-05-29' },
    { firm: '신한투자증권', rating: 'BUY', target: '2,480,000', date: '2026-05-27' },
    { firm: '미래에셋증권', rating: 'BUY', target: '2,600,000', date: '2026-05-25' },
    { firm: 'NH투자증권', rating: 'BUY', target: '2,450,000', date: '2026-05-22' },
    { firm: '한국투자증권', rating: 'HOLD', target: '2,200,000', date: '2026-05-20' },
    { firm: '삼성증권', rating: 'BUY', target: '2,580,000', date: '2026-05-18' },
    { firm: '대신증권', rating: 'BUY', target: '2,520,000', date: '2026-05-15' },
  ],
  '005930': [
    { firm: '신한투자증권', rating: 'BUY', target: '400,000', date: '2026-06-01' },
    { firm: 'NH투자증권', rating: 'HOLD', target: '380,000', date: '2026-05-28' },
    { firm: '미래에셋증권', rating: 'BUY', target: '420,000', date: '2026-05-26' },
    { firm: 'KB증권', rating: 'HOLD', target: '370,000', date: '2026-05-24' },
    { firm: '한국투자증권', rating: 'SELL', target: '340,000', date: '2026-05-22' },
    { firm: '삼성증권', rating: 'HOLD', target: '385,000', date: '2026-05-20' },
    { firm: '메리츠증권', rating: 'BUY', target: '410,000', date: '2026-05-18' },
    { firm: '대신증권', rating: 'HOLD', target: '375,000', date: '2026-05-15' },
  ],
  '035420': [
    { firm: '다올투자증권', rating: 'SELL', target: '265,000', date: '2026-05-29' },
    { firm: 'NH투자증권', rating: 'HOLD', target: '285,000', date: '2026-05-27' },
    { firm: '신한투자증권', rating: 'HOLD', target: '290,000', date: '2026-05-25' },
    { firm: 'KB증권', rating: 'SELL', target: '270,000', date: '2026-05-22' },
    { firm: '미래에셋증권', rating: 'HOLD', target: '295,000', date: '2026-05-20' },
    { firm: '한국투자증권', rating: 'SELL', target: '260,000', date: '2026-05-18' },
  ],
};

export const BROKER_HIGHLIGHTS: Record<string, BrokerReportHighlight[]> = {
  '000660': [
    {
      id: 'h1',
      firm: '메리츠증권',
      title: 'HBM3E 수율 우위, 2H26 캐파 확대 가속',
      analyst: '김반도체',
      date: '2026-06-01',
      rating: 'BUY',
      target: '2,550,000',
      excerpt:
        '엔비디아향 HBM3E 12단 적층 수율이 경쟁사 대비 15%p 우위. 2분기 CAPEX 공시가 선행 지표와 일치.',
    },
    {
      id: 'h2',
      firm: '미래에셋증권',
      title: 'AI 메모리 슈퍼사이클 2차 파고',
      analyst: '이메모리',
      date: '2026-05-25',
      rating: 'BUY',
      target: '2,600,000',
      excerpt:
        '서버 DRAM 가격 상승세 지속. HBM+eSSD 동반 성장으로 2026 EPS 상향 조정.',
    },
    {
      id: 'h3',
      firm: '한국투자증권',
      title: '밸류에이션 부담, 단기 조정 가능',
      analyst: '박밸류',
      date: '2026-05-20',
      rating: 'HOLD',
      target: '2,200,000',
      excerpt: '목표 PER 25배 적용 시 현 주가 대비 제한적 업사이드. 수급 과열 주의.',
    },
  ],
  '005930': [
    {
      id: 's1',
      firm: '신한투자증권',
      title: 'HBM3 12단 양산 시점, 벤더 테스트 관건',
      analyst: '최반도체',
      date: '2026-06-01',
      rating: 'BUY',
      target: '400,000',
      excerpt: '3분기 HBM3 12단 본격 양산 시 수익성 개선 기대. 파운드리 적자 축소 전망.',
    },
    {
      id: 's2',
      firm: '한국투자증권',
      title: 'HBM 검증 지연 리스크 부각',
      analyst: '정리스크',
      date: '2026-05-22',
      rating: 'SELL',
      target: '340,000',
      excerpt: '주요 고객사 벤더 테스트 연기 루머 확산. 메모리 부문 회복 시점 불확실.',
    },
    {
      id: 's3',
      firm: 'NH투자증권',
      title: '스마트폰·가전 회복 vs 메모리 불확실',
      analyst: '윤종합',
      date: '2026-05-28',
      rating: 'HOLD',
      target: '380,000',
      excerpt: '비메모리 부문은 점진적 회복이나, HBM 의존도 상승으로 변동성 확대.',
    },
  ],
  '035420': [
    {
      id: 'n1',
      firm: '다올투자증권',
      title: 'AI 인프라 투자 부담, 마진 압박 지속',
      analyst: '강인터넷',
      date: '2026-05-29',
      rating: 'SELL',
      target: '265,000',
      excerpt: '클라우드·AI CAPEX 급증으로 영업이익률 하락. 광고 시장 경쟁 심화.',
    },
    {
      id: 'n2',
      firm: '미래에셋증권',
      title: '핀테크·커머스 회복 기대 vs 비용 부담',
      analyst: '조플랫폼',
      date: '2026-05-20',
      rating: 'HOLD',
      target: '295,000',
      excerpt: '페이·커머스 거래액 반등 조짐이나, AI 투자 비용이 단기 실적을 제약.',
    },
  ],
};

export const CONSENSUS_TREND_LABELS = ['3월', '4월', '5월', '6월'];

export function parseTargetPrice(target: string): number {
  return Number(target.replace(/,/g, ''));
}

export function calcConsensusStats(
  opinions: ReportOpinion[],
  currentPrice: number,
): {
  avgTarget: number;
  upsidePct: number;
  buy: number;
  hold: number;
  sell: number;
  total: number;
} {
  const targets = opinions.map((o) => parseTargetPrice(o.target));
  const avgTarget = Math.round(targets.reduce((a, b) => a + b, 0) / targets.length);
  const upsidePct = Number((((avgTarget - currentPrice) / currentPrice) * 100).toFixed(1));
  const buy = opinions.filter((o) => o.rating === 'BUY').length;
  const hold = opinions.filter((o) => o.rating === 'HOLD').length;
  const sell = opinions.filter((o) => o.rating === 'SELL').length;
  return { avgTarget, upsidePct, buy, hold, sell, total: opinions.length };
}
