# Signal α 기획 문서 (planning)

> 노션 기획안(재재수정안, 2026-06-12 내보내기)을 레포 구현 현실에 맞춰 정합화한 통합본.
> 정리일: 2026-06-12 (main 기준)

## 읽기 전 원칙

- **코드가 사실 기준(source of truth)이다.** 본 문서는 기획 의도와 구현 현실을 합친 정합본이며, 코드와 어긋나는 서술을 발견하면 코드를 기준으로 본 문서를 고친다.
- 아직 구현되지 않은 항목은 본문에 **`[계획]`** 라벨로 표시했다. 라벨이 없는 항목은 main 브랜치에 구현이 존재한다.
- `docs/project-context.md`는 초기 기획 스냅샷(구버전)이다. 기획 내용은 본 폴더가 우선한다.

## 문서 구성

| 문서 | 내용 |
| --- | --- |
| [00_메인_기획서.md](00_메인_기획서.md) | 프로젝트 개요·차별화·구조·로드맵 (메인) |
| [04_수집단계_Collector.md](04_수집단계_Collector.md) | 수집기 6종 명세, 소스별 수집 기간 |
| [05_분석단계_Analyzer.md](05_분석단계_Analyzer.md) | 공식·대안·주가 분석기 상세 |
| [06_통합단계_Aggregator.md](06_통합단계_Aggregator.md) | Debate 5방식, 토론 구조, 실패 정책 `[계획]` |
| [07_화면시각화설계.md](07_화면시각화설계.md) | 대시보드, 차트, 백테스팅 탭 `[계획]` |
| [08_모델선정.md](08_모델선정.md) | LLM·임베딩 후보 비교, 단가 |
| [09_DB설계.md](09_DB설계.md) | DB 설계 — 실제 마이그레이션 베이스라인 기준 |

## 관련 문서 (구현 기준)

- 아키텍처·수집 데몬: [`docs/architecture.md`](../architecture.md), [`docs/price-collector.md`](../price-collector.md), [`docs/spec/kiwoom-rest-spec.md`](../../spec/kiwoom-rest-spec.md)
- DB 상세: [`database/README.md`](../../../database/README.md), [`database/erd/signal_alpha_core_erd.md`](../../../database/erd/signal_alpha_core_erd.md)

## 노션 원본

노션 워크스페이스의 "재재수정안 기획서" 페이지가 원본이다. 단계별 Mermaid ERD 이미지(7장, 각 1~2.5MB)는 용량 문제로 레포에 포함하지 않았다 — ERD는 [`database/erd/signal_alpha_core_erd.md`](../../../database/erd/signal_alpha_core_erd.md)를 참조한다.

---

*팀 LENS — Link · Evidence · Navigate · Signal*
