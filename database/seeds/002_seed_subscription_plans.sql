-- 구독 플랜 시드 (신규 기획: 비구독 free + 단일 구독 monthly_9900)
-- 주의: migrate.py 는 migrations 적용 후 seeds 를 실행한다. 따라서 fresh DB 에서는
--   021_subscription_single_product.sql 의 UPDATE(free/pro/premium)가 빈 테이블을 만나 no-op 이 되고,
--   이 시드가 최종 plan 행을 만든다. 그러므로 이 시드 자체가 신규 모델의 정본이어야 한다.
-- 기존 DB(과거 free=3/pro/premium 시드됨)는 ON CONFLICT DO NOTHING 으로 보존되고,
--   021 마이그레이션이 free 무제한화·pro/premium 비활성·monthly_9900 upsert 로 전환한다.
-- max_watchlist 2147483647 = INT 최대값("무제한" 표기). 백엔드는 관심종목 한도를 검사하지 않는다.
INSERT INTO subscription_plans (
    plan_type,
    plan_display_name,
    max_watchlist,
    signal_delay_hours,
    journal_max_entries,
    has_alt_data,
    has_detail_report,
    has_backtesting,
    price_monthly,
    price_yearly,
    is_active
) VALUES
    (
        'free',
        'Free',
        2147483647,
        24,
        50,
        FALSE,
        FALSE,
        FALSE,
        0,
        0,
        TRUE
    ),
    (
        'monthly_9900',
        '월 구독',
        2147483647,
        0,
        2147483647,
        TRUE,
        TRUE,
        FALSE,
        9900,
        0,
        TRUE
    )
ON CONFLICT (plan_type) DO NOTHING;
