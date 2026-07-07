-- target: backend
-- 데모/발표용 published final_signals 시드 (신선 DB에서도 화면이 비지 않게).
--
-- 안전장치: final_signals 가 **비어 있을 때만**(cold DB) 삽입한다 — 실제 발행 신호가 하나라도
--   있는 DB(운영/스테이징)에서는 통째로 no-op 이라 데모 데이터가 프로덕션을 오염시키지 않는다.
--   재실행해도 두 번째부터는 이미 채워져 있어 no-op(idempotent).
-- 대표 5종목의 **비중립** 신호. score_breakdown 은 라이브 aggregator 출력과 동일 스키마:
--   _meta(headline_method/scoring_method/blend_basis) + 소스별 contributes_to_score 포함 →
--   FE/LLM 이 "학습 결합" vs "참고용 평균" 라벨·"점수가 X인 이유" 근거를 데모에서도 그대로 렌더한다.
-- 산출방식은 deterministic_blend(참고용 소스 평균) — 메타러너 artifact 없이도 정직하게 표기.
-- 소스 결과가 아예 없는 cold DB 대비용. 파이프라인을 돌린 DB 는 _headline 폴백이 실데이터로 채운다.

WITH demo(
    code, final_score, confidence, consensus, signal, agreement, warning, needs_review,
    base_score, breakdown, summary, bull, bear, pos_ev, caut_ev
) AS (
    VALUES
    -- ① 삼성전자 — 다중 소스 상승 정렬(HIGH)
    ('005930', 62.00, 80.00, 80.00, 'positive', 'HIGH', 'NORMAL', false, 62.00,
     '{"DART":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"신규 사업보고서·투자 관련 공시 확인(점수 미반영, 근거)","data_age_days":0,"contributes_to_score":false},"PRICE":{"direction":"positive","score":0.15,"score_100":57.5,"data_status":"ok","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"외국인·기관 순매수 흐름","data_age_days":0,"contributes_to_score":false},"REPORT":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"증권사 목표주가 분포 참고","data_age_days":0,"contributes_to_score":false},"HIRING":{"direction":"positive","score":0.30,"score_100":65.0,"data_status":"ok","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"핵심 직무 채용 공고 증가 — 확장 흔적","data_age_days":1,"contributes_to_score":true},"PATENT":{"direction":"positive","score":0.24,"score_100":62.0,"data_status":"ok","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"신규 분야 특허 출원 증가","data_age_days":2,"contributes_to_score":true},"DATALAB":{"direction":"positive","score":0.18,"score_100":59.0,"data_status":"ok","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"제품 검색 관심도 상승","data_age_days":0,"contributes_to_score":true},"_meta":{"headline_method":"deterministic_blend","scoring_method":"deterministic_blend","blend_basis":{"sources":[{"source":"HIRING","signed_score":0.30,"share":0.3333},{"source":"PATENT","signed_score":0.24,"share":0.3333},{"source":"DATALAB","signed_score":0.18,"share":0.3333}],"blend_score":0.24}}}'::jsonb,
     '채용·특허·검색 관심도가 함께 상승 방향을 가리킵니다. 소스 간 방향이 대체로 일치합니다.',
     '핵심 직무 채용 증가와 신규 분야 특허 출원이 확장 흔적으로 관측됩니다.',
     '가격 지표는 참고용이며 점수에 반영하지 않았습니다.',
     '[{"source":"HIRING","summary":"핵심 직무 채용 공고 증가","source_signal_event_ids":[]},{"source":"PATENT","summary":"신규 분야 특허 출원 증가","source_signal_event_ids":[]},{"source":"DATALAB","summary":"제품 검색 관심도 상승","source_signal_event_ids":[]}]'::jsonb,
     '[{"source":"DART","risk_flags":[],"summary":"공시는 근거로만 표시(점수 미반영)"}]'::jsonb),
    -- ② SK하이닉스 — 상승 정렬이지만 소스 커버리지 얕음(MEDIUM)
    ('000660', 61.50, 66.00, 66.00, 'positive', 'MEDIUM', 'NORMAL', false, 61.50,
     '{"DART":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"공시 근거(점수 미반영)","data_age_days":0,"contributes_to_score":false},"PRICE":{"direction":"positive","score":0.10,"score_100":55.0,"data_status":"ok","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"거래량 동반 상승","data_age_days":0,"contributes_to_score":false},"REPORT":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"목표주가 분포 참고","data_age_days":0,"contributes_to_score":false},"HIRING":{"direction":"positive","score":0.36,"score_100":68.0,"data_status":"ok","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"생산·연구 직무 채용 확대","data_age_days":1,"contributes_to_score":true},"PATENT":{"direction":"positive","score":0.10,"score_100":55.0,"data_status":"ok","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"공정 관련 특허 소폭 증가","data_age_days":3,"contributes_to_score":true},"DATALAB":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"검색 관심도 변화 미미","data_age_days":0,"contributes_to_score":false},"_meta":{"headline_method":"deterministic_blend","scoring_method":"deterministic_blend","blend_basis":{"sources":[{"source":"HIRING","signed_score":0.36,"share":0.5},{"source":"PATENT","signed_score":0.10,"share":0.5}],"blend_score":0.23}}}'::jsonb,
     '채용과 특허가 상승 방향을 가리키나, 관측된 소스가 적어 추가 확인이 필요합니다.',
     '생산·연구 직무 채용 확대가 확장 흔적으로 관측됩니다.',
     '검색 관심도 변화가 뚜렷하지 않아 커버리지가 얕습니다.',
     '[{"source":"HIRING","summary":"생산·연구 직무 채용 확대","source_signal_event_ids":[]},{"source":"PATENT","summary":"공정 관련 특허 소폭 증가","source_signal_event_ids":[]}]'::jsonb,
     '[{"source":"DATALAB","risk_flags":["thin_coverage"],"summary":"검색 관심도 신호 약함"}]'::jsonb),
    -- ③ NAVER — 소스 간 방향 불일치(mixed, CAUTION)
    ('035420', 48.75, 50.00, 50.00, 'mixed', 'LOW', 'CAUTION', true, 48.75,
     '{"DART":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"공시 근거(점수 미반영)","data_age_days":0,"contributes_to_score":false},"PRICE":{"direction":"negative","score":-0.10,"score_100":45.0,"data_status":"ok","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"단기 가격 약세","data_age_days":0,"contributes_to_score":false},"REPORT":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"목표주가 분포 참고","data_age_days":0,"contributes_to_score":false},"HIRING":{"direction":"positive","score":0.20,"score_100":60.0,"data_status":"ok","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"신사업 직무 채용 증가","data_age_days":2,"contributes_to_score":true},"PATENT":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"특허 신호 없음","data_age_days":0,"contributes_to_score":false},"DATALAB":{"direction":"negative","score":-0.25,"score_100":37.5,"data_status":"ok","needs_review":true,"analysis_result_id":null,"agent_result_id":null,"risk_flags":["risk_interest_up"],"summary":"부정 이슈 관련 검색 관심도 상승","data_age_days":0,"contributes_to_score":true},"_meta":{"headline_method":"deterministic_blend","scoring_method":"deterministic_blend","blend_basis":{"sources":[{"source":"HIRING","signed_score":0.20,"share":0.5},{"source":"DATALAB","signed_score":-0.25,"share":0.5}],"blend_score":-0.025}}}'::jsonb,
     '채용은 상승, 검색 관심도는 하락으로 소스 간 방향이 엇갈립니다. 추가 확인이 필요합니다.',
     '신사업 직무 채용 증가가 관측됩니다.',
     '부정 이슈 관련 검색 관심도 상승 — 주의 근거입니다.',
     '[{"source":"HIRING","summary":"신사업 직무 채용 증가","source_signal_event_ids":[]}]'::jsonb,
     '[{"source":"DATALAB","risk_flags":["risk_interest_up"],"summary":"부정 이슈 검색 관심도 상승"},{"source":"PRICE","risk_flags":[],"summary":"단기 가격 약세"}]'::jsonb),
    -- ④ 카카오 — 하락 정렬(negative, MEDIUM)
    ('035720', 38.75, 66.00, 66.00, 'negative', 'MEDIUM', 'CAUTION', true, 38.75,
     '{"DART":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"공시 근거(점수 미반영)","data_age_days":0,"contributes_to_score":false},"PRICE":{"direction":"negative","score":-0.12,"score_100":44.0,"data_status":"ok","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"기관 순매도","data_age_days":0,"contributes_to_score":false},"REPORT":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"목표주가 분포 참고","data_age_days":0,"contributes_to_score":false},"HIRING":{"direction":"negative","score":-0.30,"score_100":35.0,"data_status":"ok","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":["hiring_slowdown"],"summary":"채용 공고 축소","data_age_days":1,"contributes_to_score":true},"PATENT":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"특허 신호 없음","data_age_days":0,"contributes_to_score":false},"DATALAB":{"direction":"negative","score":-0.15,"score_100":42.5,"data_status":"ok","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"검색 관심도 둔화","data_age_days":0,"contributes_to_score":true},"_meta":{"headline_method":"deterministic_blend","scoring_method":"deterministic_blend","blend_basis":{"sources":[{"source":"HIRING","signed_score":-0.30,"share":0.5},{"source":"DATALAB","signed_score":-0.15,"share":0.5}],"blend_score":-0.225}}}'::jsonb,
     '채용 축소와 검색 관심도 둔화가 함께 하락 방향을 가리킵니다. 주의가 필요합니다.',
     '뚜렷한 상승 근거가 관측되지 않습니다.',
     '채용 공고 축소가 주의 근거로 관측됩니다.',
     '[]'::jsonb,
     '[{"source":"HIRING","risk_flags":["hiring_slowdown"],"summary":"채용 공고 축소"},{"source":"DATALAB","risk_flags":[],"summary":"검색 관심도 둔화"}]'::jsonb),
    -- ⑤ 현대차 — 단일 소스·약한 신호(neutral, LOW)
    ('005380', 52.50, 50.00, 50.00, 'neutral', 'LOW', 'CAUTION', true, 52.50,
     '{"DART":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"공시 근거(점수 미반영)","data_age_days":0,"contributes_to_score":false},"PRICE":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"가격 신호 없음","data_age_days":0,"contributes_to_score":false},"REPORT":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"missing","needs_review":true,"analysis_result_id":null,"agent_result_id":null,"risk_flags":["missing_source"],"summary":null,"contributes_to_score":false},"HIRING":{"direction":"positive","score":0.05,"score_100":52.5,"data_status":"ok","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"채용 소폭 증가","data_age_days":3,"contributes_to_score":true},"PATENT":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"특허 신호 없음","data_age_days":0,"contributes_to_score":false},"DATALAB":{"direction":"neutral","score":0.0,"score_100":50.0,"data_status":"no_signal","needs_review":false,"analysis_result_id":null,"agent_result_id":null,"risk_flags":[],"summary":"검색 관심도 변화 미미","data_age_days":0,"contributes_to_score":false},"_meta":{"headline_method":"deterministic_blend","scoring_method":"deterministic_blend","blend_basis":{"sources":[{"source":"HIRING","signed_score":0.05,"share":1.0}],"blend_score":0.05}}}'::jsonb,
     '관측된 소스가 하나뿐이라 방향을 단정하기 어렵습니다. 추가 확인이 필요합니다.',
     '채용이 소폭 증가했습니다.',
     '단일 소스 기반이라 신뢰도가 낮습니다.',
     '[{"source":"HIRING","summary":"채용 소폭 증가","source_signal_event_ids":[]}]'::jsonb,
     '[{"source":"REPORT","risk_flags":["missing_source"],"summary":null}]'::jsonb)
),
resolved AS (
    SELECT d.*, s.id AS stock_id
    FROM demo d
    JOIN public.stocks s ON s.ticker = d.code
    -- cold DB 가드: 실제 발행 신호가 하나라도 있으면 전체 no-op(프로덕션 오염 방지).
    WHERE NOT EXISTS (SELECT 1 FROM public.final_signals)
),
ins_ar AS (
    INSERT INTO public.analysis_results
        (stock_id, analysis_date, run_key, source_signal_event_ids, base_score, analysis_mode, version)
    SELECT stock_id, CURRENT_DATE, 'AGGREGATED', '{}'::bigint[], base_score, 'full', 'final-agg-v1'
    FROM resolved
    RETURNING id, stock_id
)
INSERT INTO public.final_signals
    (stock_id, analysis_result_id, signal_date, run_key, version, is_current,
     final_score, confidence, signal, source_agreement, warning_level, score_breakdown,
     summary, bull_point, bear_point, needs_review, is_published, published_at,
     consensus_score, positive_evidence, caution_evidence)
SELECT r.stock_id, a.id, CURRENT_DATE, 'AGGREGATED', 'final-agg-v1', true,
       r.final_score, r.confidence, r.signal, r.agreement, r.warning, r.breakdown,
       r.summary, r.bull, r.bear, r.needs_review, true, now(),
       r.consensus, r.pos_ev, r.caut_ev
FROM resolved r
JOIN ins_ar a ON a.stock_id = r.stock_id;
