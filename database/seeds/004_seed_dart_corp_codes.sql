INSERT INTO dart_corp_codes (
    stock_id,
    corp_code,
    ticker,
    corp_name,
    corp_name_eng,
    stock_name,
    is_active,
    synced_at
)
SELECT
    stocks.id,
    seed.corp_code,
    seed.ticker,
    seed.corp_name,
    seed.corp_name_eng,
    seed.stock_name,
    TRUE,
    NOW()
FROM (
    VALUES
        ('005930', '00126380', '삼성전자', 'SAMSUNG ELECTRONICS CO.,LTD.', '삼성전자'),
        ('000660', '00164779', 'SK하이닉스', 'SK hynix Inc.', 'SK하이닉스'),
        ('035420', '00266961', 'NAVER', 'NAVER Corporation', 'NAVER')
) AS seed(ticker, corp_code, corp_name, corp_name_eng, stock_name)
INNER JOIN stocks
    ON stocks.ticker = seed.ticker
ON CONFLICT (corp_code)
DO UPDATE SET
    stock_id = EXCLUDED.stock_id,
    ticker = EXCLUDED.ticker,
    corp_name = EXCLUDED.corp_name,
    corp_name_eng = EXCLUDED.corp_name_eng,
    stock_name = EXCLUDED.stock_name,
    is_active = EXCLUDED.is_active,
    synced_at = EXCLUDED.synced_at,
    updated_at = NOW();
