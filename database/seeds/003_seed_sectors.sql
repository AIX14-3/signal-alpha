-- MVP 업종 + 코스피 종합지수 시드.
-- 키움 업종코드 기준 (001: 코스피 종합, 003 운수장비, 004 전기전자,
-- 006 철강금속, 022 금융업, 047 서비스업).

INSERT INTO sectors (kiwoom_code, market, name, is_market_index) VALUES
    ('001', 'KOSPI', '코스피 종합', TRUE),
    ('003', 'KOSPI', '운수장비', FALSE),
    ('004', 'KOSPI', '전기전자', FALSE),
    ('006', 'KOSPI', '철강금속', FALSE),
    ('022', 'KOSPI', '금융업', FALSE),
    ('047', 'KOSPI', '서비스업', FALSE)
ON CONFLICT (market, kiwoom_code) DO NOTHING;

-- MVP 종목 → 업종 연결 (시드된 종목에 한해 적용).
WITH mapping (ticker, kiwoom_code) AS (
    VALUES
        ('005930', '004'),
        ('000660', '004'),
        ('035420', '047'),
        ('005380', '003'),
        ('105560', '022'),
        ('005490', '006')
)
UPDATE stocks s
SET sector_id = sec.id
FROM mapping m
JOIN sectors sec ON sec.market = 'KOSPI' AND sec.kiwoom_code = m.kiwoom_code
WHERE s.ticker = m.ticker;
