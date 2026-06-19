-- 006_seed_hiring_job_functions.sql
--
-- Seeds hiring_job_functions and hiring_job_function_stocks (이슈 #299).
-- 신규 analyzer의 sector_demand(섹터 직무 수요) 신호를 활성화한다: peer 종목의
-- 직무별 채용 모멘텀을 전파해 own-momentum을 보완한다. 매핑이 비어 있으면
-- list_hiring_function_weights()가 빈 dict → _sector_demand=None → 신호가 죽는다.
--
-- 표준 직무 키/라벨의 출처(Source of truth):
--   app/analyzers/hiring/job_functions.py 의 DEFAULT_FUNCTION_RULES 키 + LABELS.
--   여기 function_key는 그 분류기가 산출하는 키와 정확히 일치해야 한다(드리프트 시
--   해당 직무는 unmapped로 떨어져 신호에 기여하지 못함).
--
-- weight 의미: 그 종목이 의존하는 직무의 "상대 노출치"다. 분석기는
--   weighted_sum/coverage(가중평균)로 합치므로 절대 스케일은 무관(정규화 불필요).
--   peer rows는 전 유니버스(타깃 제외)라, weight 프로파일이 종목별 신호를 차별화한다.
--   generic 직무(CORPORATE/SALES 등)는 전 섹터 공통이라 노이즈 → 대체로 미매핑.
--
-- 재실행 안전(idempotent): 모든 INSERT에 ON CONFLICT … DO NOTHING.
-- stock_id는 stocks.ticker로 해석 — 절대 하드코딩 금지. 티커는 문자열 리터럴로
--   유지(앞자리 0 보존; 무따옴표 시 정수 해석 → varchar 조인 실패).

-- -------------------------------------------------------------------------
-- 1. 표준 직무 (10개) — 코드 LABELS와 정확히 일치
-- -------------------------------------------------------------------------

INSERT INTO hiring_job_functions (function_key, label, is_active)
VALUES
    ('DATA_AI',       '데이터/AI',            TRUE),
    ('ENGINEER',      '개발/엔지니어',        TRUE),
    ('RESEARCH',      '연구개발(R&D)',        TRUE),
    ('MANUFACTURING', '생산/제조',            TRUE),
    ('CREATIVE',      '콘텐츠/크리에이티브',  TRUE),
    ('SALES',         '영업',                 TRUE),
    ('MARKETING',     '마케팅',               TRUE),
    ('DESIGN',        '디자인',               TRUE),
    ('PLANNING',      '기획/전략',            TRUE),
    ('CORPORATE',     '경영지원',             TRUE)
ON CONFLICT (function_key) DO NOTHING;

-- -------------------------------------------------------------------------
-- 2. 종목 → 직무 노출 가중치 (섹터별, 58행)
--    stock_id는 stocks.ticker로 해석.
-- -------------------------------------------------------------------------

INSERT INTO hiring_job_function_stocks (job_function_id, stock_id, weight)
SELECT jf.id, s.id, v.weight
FROM hiring_job_functions jf
JOIN (VALUES
    -- 반도체 (삼성전자 005930 · SK하이닉스 000660)
    ('ENGINEER',      '005930', 1.0),
    ('MANUFACTURING', '005930', 1.0),
    ('DATA_AI',       '005930', 0.8),
    ('RESEARCH',      '005930', 0.7),
    ('ENGINEER',      '000660', 1.0),
    ('MANUFACTURING', '000660', 1.0),
    ('DATA_AI',       '000660', 0.8),
    ('RESEARCH',      '000660', 0.7),
    -- 반도체장비 (한미반도체 042700)
    ('MANUFACTURING', '042700', 1.0),
    ('ENGINEER',      '042700', 0.8),
    ('RESEARCH',      '042700', 0.5),
    -- 인터넷/플랫폼 (NAVER 035420 · 카카오 035720)
    ('ENGINEER',      '035420', 1.0),
    ('DATA_AI',       '035420', 1.0),
    ('PLANNING',      '035420', 0.7),
    ('DESIGN',        '035420', 0.6),
    ('MARKETING',     '035420', 0.5),
    ('ENGINEER',      '035720', 1.0),
    ('DATA_AI',       '035720', 1.0),
    ('PLANNING',      '035720', 0.7),
    ('DESIGN',        '035720', 0.6),
    ('MARKETING',     '035720', 0.5),
    -- 게임 (크래프톤 259960)
    ('ENGINEER',      '259960', 1.0),
    ('DATA_AI',       '259960', 0.8),
    ('CREATIVE',      '259960', 0.7),
    ('DESIGN',        '259960', 0.6),
    ('PLANNING',      '259960', 0.5),
    -- 자동차 (현대자동차 005380 · 기아 000270)
    ('ENGINEER',      '005380', 1.0),
    ('MANUFACTURING', '005380', 1.0),
    ('DATA_AI',       '005380', 0.6),
    ('RESEARCH',      '005380', 0.6),
    ('ENGINEER',      '000270', 1.0),
    ('MANUFACTURING', '000270', 1.0),
    ('DATA_AI',       '000270', 0.6),
    ('RESEARCH',      '000270', 0.6),
    -- 자동차부품 (HL만도 204320)
    ('MANUFACTURING', '204320', 1.0),
    ('ENGINEER',      '204320', 0.9),
    ('RESEARCH',      '204320', 0.5),
    -- 엔터 (HYBE 352820 · SM엔터테인먼트 041510)
    ('CREATIVE',      '352820', 1.0),
    ('MARKETING',     '352820', 0.8),
    ('PLANNING',      '352820', 0.6),
    ('DESIGN',        '352820', 0.5),
    ('CREATIVE',      '041510', 1.0),
    ('MARKETING',     '041510', 0.8),
    ('PLANNING',      '041510', 0.6),
    ('DESIGN',        '041510', 0.5),
    -- 콘텐츠 (스튜디오드래곤 253450)
    ('CREATIVE',      '253450', 1.0),
    ('PLANNING',      '253450', 0.7),
    ('MARKETING',     '253450', 0.5),
    -- 바이오 (삼성바이오로직스 207940 · 셀트리온 068270)
    ('RESEARCH',      '207940', 1.0),
    ('MANUFACTURING', '207940', 0.8),
    ('ENGINEER',      '207940', 0.4),
    ('RESEARCH',      '068270', 1.0),
    ('MANUFACTURING', '068270', 0.8),
    ('ENGINEER',      '068270', 0.4),
    -- 제약 (유한양행 000100)
    ('RESEARCH',      '000100', 1.0),
    ('MANUFACTURING', '000100', 0.7),
    ('ENGINEER',      '000100', 0.4),
    ('SALES',         '000100', 0.4)
) AS v(function_key, ticker, weight)
    ON jf.function_key = v.function_key
JOIN stocks s ON s.ticker = v.ticker
ON CONFLICT (job_function_id, stock_id) DO NOTHING;
