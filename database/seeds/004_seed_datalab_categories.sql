-- 004_seed_datalab_categories.sql
-- target: collection
-- (datalab_* 는 COLLECTION 전용 — 수집 DB 만 시드)
--
-- Seeds datalab_categories and datalab_category_stocks.
-- Source of truth: .claude/skills/signal-alpha-collector/references/category-registry.md
--
-- Safe to re-run: ON CONFLICT … DO NOTHING on all inserts.
-- stock_id is resolved from stocks.ticker at insert time — never hardcoded.

-- -------------------------------------------------------------------------
-- 1. Categories (13 total)
-- -------------------------------------------------------------------------

INSERT INTO datalab_categories (name, sector, description, is_active)
VALUES
    -- Semiconductor / Memory (삼성전자 · SK하이닉스)
    ('HBM',                'SEMICONDUCTOR', 'HBM and high-bandwidth memory search trends',       TRUE),
    ('AI_SEMICONDUCTOR',   'SEMICONDUCTOR', 'AI chip, NPU, GPU semiconductor search trends',     TRUE),
    ('MEMORY_CORE',        'SEMICONDUCTOR', 'DRAM, NAND and core memory search trends',           TRUE),
    ('SEMICONDUCTOR_CYCLE','SEMICONDUCTOR', 'Memory supercycle, pricing and inventory trends',   TRUE),
    ('SAMSUNG_BRAND',      'SEMICONDUCTOR', 'Samsung Electronics brand search interest',         TRUE),
    ('HYNIX_BRAND',        'SEMICONDUCTOR', 'SK Hynix brand search interest',                   TRUE),
    ('SAMSUNG_MOBILE',     'SEMICONDUCTOR', 'Galaxy product line search trends',                 TRUE),
    ('AI_SERVER',          'SEMICONDUCTOR', 'AI server and datacenter memory search trends',     TRUE),
    -- Platform / AI (네이버)
    ('NAVER_BRAND',        'PLATFORM', 'Naver corporate brand search interest',                  TRUE),
    ('AI_SEARCH',          'PLATFORM', 'Naver AI and HyperCLOVA search trends',                  TRUE),
    ('CLOUD_B2B',          'PLATFORM', 'Naver Cloud B2B and sovereign AI trends',                TRUE),
    ('NAVER_COMMERCE',     'PLATFORM', 'Naver shopping and payment search trends',               TRUE),
    ('NAVER_CONTENT',      'PLATFORM', 'Naver Webtoon and content search trends',                TRUE)
ON CONFLICT (name) DO NOTHING;

-- -------------------------------------------------------------------------
-- 2. Category → stock mappings (18 rows)
--    stock_id resolved from stocks.ticker at insert time.
-- -------------------------------------------------------------------------

INSERT INTO datalab_category_stocks (category_id, stock_id, weight)
SELECT dc.id, s.id, v.weight
FROM datalab_categories dc
JOIN (VALUES
    ('HBM',                '005930', 0.8),
    ('HBM',                '000660', 1.0),
    ('AI_SEMICONDUCTOR',   '005930', 1.0),
    ('AI_SEMICONDUCTOR',   '000660', 0.9),
    ('MEMORY_CORE',        '005930', 0.9),
    ('MEMORY_CORE',        '000660', 1.0),
    ('SEMICONDUCTOR_CYCLE','005930', 1.0),
    ('SEMICONDUCTOR_CYCLE','000660', 1.0),
    ('SAMSUNG_BRAND',      '005930', 1.0),
    ('HYNIX_BRAND',        '000660', 1.0),
    ('SAMSUNG_MOBILE',     '005930', 0.7),
    ('AI_SERVER',          '005930', 0.8),
    ('AI_SERVER',          '000660', 1.0),
    ('NAVER_BRAND',        '035420', 1.0),
    ('AI_SEARCH',          '035420', 1.0),
    ('CLOUD_B2B',          '035420', 1.0),
    ('NAVER_COMMERCE',     '035420', 0.9),
    ('NAVER_CONTENT',      '035420', 0.8)
) AS v(category_name, ticker, weight)
    ON dc.name = v.category_name
JOIN stocks s ON s.ticker = v.ticker
ON CONFLICT (category_id, stock_id) DO NOTHING;
