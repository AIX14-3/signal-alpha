// 소스별 "관련 자료" 참고 링크.
//
// 중요: 여기 링크는 분석에 실제로 사용된 그 문서가 아니라, 같은 상류 데이터를 사람이
// 직접 확인할 수 있는 공개 창구다. 실제 근거 문서(evidence_url)를 가진 소스(DART·REPORT)는
// SourceDetailBody 가 "근거가 된 원본 문서" 섹션을 대신 보여주므로 이 맵을 쓰지 않는다.
//
// 상류 출처(수집기 기준):
//   price   → 키움증권 REST OpenAPI 시세 (collectors/price/kiwoom)
//   hiring  → 자소설닷컴·잡코리아·사람인 + 기업 채용페이지 크롤 (collectors/hiring/sites)
//   datalab → 네이버 데이터랩 검색어 트렌드 API (clients/naver_datalab_client)
//   patent  → KIPRIS(최신) + Google Patents BigQuery(과거이력) 이중 소스
//             (clients/kipris_client, collectors/patent/bigquery_source)

import type { SourceKey } from "@/lib/apiClient";

export type ReferenceLink = { label: string; href: string; note: string };
export type SourceReferences = { explanation: string; links: ReferenceLink[] };

function q(value: string): string {
  return encodeURIComponent(value);
}

export function referenceLinks(
  source: SourceKey,
  stockName: string,
  stockCode: string,
): SourceReferences {
  switch (source) {
    case "price":
      return {
        explanation:
          "개별 문서가 아니라 시세·수급 시계열(키움증권 OpenAPI)을 집계해 분석합니다. 아래에서 같은 종목의 시세를 직접 확인할 수 있습니다.",
        links: [
          {
            label: "네이버 금융 종목 시세",
            href: `https://finance.naver.com/item/main.naver?code=${q(stockCode)}`,
            note: "시세·거래량 원자료",
          },
        ],
      };
    case "hiring":
      return {
        explanation:
          "채용 사이트의 공고 목록을 크롤해 채용 규모·직무 구성의 변화를 집계합니다. 개별 공고는 마감되면 사라지므로 원본 링크 대신 검색 창구를 둡니다.",
        links: [
          {
            label: "잡코리아에서 검색",
            href: `https://www.jobkorea.co.kr/Search/?stext=${q(stockName)}`,
            note: "현재 열린 공고",
          },
          {
            label: "사람인에서 검색",
            href: `https://www.saramin.co.kr/zf_user/search?searchword=${q(stockName)}`,
            note: "현재 열린 공고",
          },
        ],
      };
    case "datalab":
      return {
        explanation:
          "개별 문서가 아니라 네이버 데이터랩의 검색어 트렌드(상대 지수)를 집계해 대중의 관심도 변화를 봅니다.",
        links: [
          {
            label: "네이버 데이터랩 검색어 트렌드",
            href: "https://datalab.naver.com/keyword/trendSearch.naver",
            note: "검색량 지수 원자료",
          },
        ],
      };
    case "patent":
      return {
        explanation:
          "KIPRIS(특허청, 최신 출원)와 Google Patents 공개 데이터셋(과거 이력)에서 이 기업의 특허를 집계합니다. 개별 특허 문서는 아래에서 직접 조회할 수 있습니다.",
        links: [
          {
            label: "Google Patents에서 출원인 검색",
            href: `https://patents.google.com/?assignee=${q(stockName)}`,
            note: "분석에 쓰인 것과 같은 상류 데이터",
          },
          {
            label: "KIPRIS 특허 검색",
            href: "https://www.kipris.or.kr/khome/search/searchResult.do",
            note: "특허청 공식 검색",
          },
        ],
      };
    default:
      return {
        explanation: "이 소스는 개별 원본 문서 없이 집계 지표로 분석합니다.",
        links: [],
      };
  }
}
