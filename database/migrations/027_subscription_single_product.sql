-- 021_subscription_single_product.sql
-- 신규 기획: 구독은 "월 9,900원 단일 상품" 1종. 관심종목 한도는 회원/유료 무관 무제한.
-- 결정:
--   - 'monthly_9900' 단일 구독 상품을 추가/보강한다(무제한 열람·무제한 관심종목).
--   - 'free'(비구독)는 관심종목 무제한으로 조정한다(기존 3 → 무제한).
--   - 구 모델 'pro'/'premium' 은 행을 삭제하지 않고 is_active=FALSE 로 비활성(과거 signal_subscriptions FK 보존).
-- max_watchlist 컬럼은 INTEGER NOT NULL 이므로 "무제한"은 INT 최대값(2147483647)으로 표현한다.
-- (백엔드는 한도 검사를 수행하지 않는 것을 정본으로 하며, 이 값은 표시/하위호환용 상한일 뿐이다.)

-- 1) 단일 구독 상품 upsert (무제한 관심종목·지연 0·대체데이터/상세리포트 제공)
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
) VALUES (
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
ON CONFLICT (plan_type) DO UPDATE SET
    plan_display_name = EXCLUDED.plan_display_name,
    max_watchlist     = EXCLUDED.max_watchlist,
    signal_delay_hours = EXCLUDED.signal_delay_hours,
    journal_max_entries = EXCLUDED.journal_max_entries,
    has_alt_data      = EXCLUDED.has_alt_data,
    has_detail_report = EXCLUDED.has_detail_report,
    price_monthly     = EXCLUDED.price_monthly,
    price_yearly      = EXCLUDED.price_yearly,
    is_active         = TRUE;

-- 2) 무료(비구독) 플랜: 관심종목 무제한
UPDATE subscription_plans
   SET max_watchlist = 2147483647,
       is_active = TRUE
 WHERE plan_type = 'free';

-- 3) 구 모델 플랜 비활성(행 보존)
UPDATE subscription_plans
   SET is_active = FALSE
 WHERE plan_type IN ('pro', 'premium');
