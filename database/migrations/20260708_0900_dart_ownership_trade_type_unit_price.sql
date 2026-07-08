-- 20260708_0900_dart_ownership_trade_type_unit_price.sql
-- target: collection
-- ============================================================================
-- dart_ownership_events 에 거래유형(trade_type)·취득처분단가(unit_price) 컬럼 추가
-- ----------------------------------------------------------------------------
-- 배경: 내부자 지분변동은 shares_delta 부호로 순매수/순매도 방향을 이미 잡는다(B-lite,
--   PR #805). 하지만 그 증감엔 장내매수/매도(진짜 시장 확신)뿐 아니라 증여·상속·스톡옵션
--   행사·신규선임 같은 비-시장 사유가 섞여 노이즈가 된다. DART 세부변동내역 표(공시 본문)의
--   "보고사유"(RPT_RSN 코드)로 거래유형을, "취득/처분단가"(ACI_AMT2)로 단가를 결정론적으로
--   뽑아 저장하면(코드 enum: 01=장내매수·02=장내매도·34=증여·58=주식매수선택권…), 정규화가
--   장내매수/매도만 방향으로 쓰고 나머지는 중립 처리하는 정밀 필터가 성립한다.
-- 설계: 값은 무료 구조화 API(elestock/majorstock)에 없고 공시 본문 document.xml 의 세부변동
--   내역 표에만 있어, 별도 결정론 파서(collectors/dart/ownership_detail.py)가 신고 건당 본문을
--   받아 채운다. 컬럼은 additive·nullable(미enrich 행/파싱 실패는 NULL → B-lite 거동 유지).
--   trade_type 은 파서 정규화 enum(닫힌 어휘)이라 chk_ 로 가드.
-- ============================================================================

ALTER TABLE public.dart_ownership_events
    ADD COLUMN IF NOT EXISTS trade_type VARCHAR(20),
    ADD COLUMN IF NOT EXISTS unit_price NUMERIC(20, 4);

-- trade_type 닫힌 어휘(파서가 RPT_RSN 코드→정규화). NULL 허용(미enrich/파싱실패).
ALTER TABLE public.dart_ownership_events
    DROP CONSTRAINT IF EXISTS chk_dart_ownership_trade_type;
ALTER TABLE public.dart_ownership_events
    ADD CONSTRAINT chk_dart_ownership_trade_type CHECK (
        trade_type IS NULL OR trade_type IN (
            'onmarket_buy',   -- 장내매수(+)
            'onmarket_sell',  -- 장내매도(-)
            'gift',           -- 증여(-)
            'gift_received',  -- 수증(+)
            'inheritance',    -- 상속
            'stock_option',   -- 주식매수선택권 행사
            'appointment',    -- 신규선임/신규보고(초기보고)
            'offmarket',      -- 장외매매
            'mixed',          -- 한 보고서에 시장·비시장 혼재
            'other'           -- 기타/미매핑
        )
    );

COMMENT ON COLUMN public.dart_ownership_events.trade_type IS
    '세부변동내역 보고사유(RPT_RSN) 정규화 거래유형. onmarket_buy/sell 만 방향 신호, 나머지는 중립.';
COMMENT ON COLUMN public.dart_ownership_events.unit_price IS
    '세부변동내역 취득/처분단가(ACI_AMT2, 주당 KRW). 장내매매 행 대표값. 크기/보조용.';
