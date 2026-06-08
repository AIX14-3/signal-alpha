INSERT INTO stocks (
    ticker,
    name,
    market,
    sector
) VALUES
    ('005930', '삼성전자', 'KOSPI', '반도체'),
    ('000660', 'SK하이닉스', 'KOSPI', '반도체'),
    ('035420', 'NAVER', 'KOSPI', '인터넷')
ON CONFLICT (ticker) DO NOTHING;
