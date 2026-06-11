-- stocks 테이블에 DataLab 검색어용 약칭 컬럼 추가
-- keyword_generator.py 의 _SHORT_NAME_MAP 데이터를 DB로 이전 (Single Source of Truth)
-- short_name NULL → 약칭 없음, full name 4개 키워드만 생성
-- short_name 있음 → "{short_name} 채용" 키워드 1개 추가

ALTER TABLE stocks ADD COLUMN IF NOT EXISTS short_name VARCHAR(50);

-- 약칭이 의미 있는 8개 기업만 업데이트
-- (나머지 7개: 카카오/크래프톤/기아/한미반도체/스튜디오드래곤/셀트리온/유한양행 → NULL 유지)
UPDATE stocks SET short_name = '삼성'    WHERE name = '삼성전자';
UPDATE stocks SET short_name = '하이닉스' WHERE name = 'SK하이닉스';
UPDATE stocks SET short_name = '삼성바이오' WHERE name = '삼성바이오로직스';
UPDATE stocks SET short_name = 'SM'      WHERE name = 'SM엔터테인먼트';
UPDATE stocks SET short_name = '현대'    WHERE name = '현대자동차';
UPDATE stocks SET short_name = '네이버'  WHERE name = 'NAVER';
UPDATE stocks SET short_name = '하이브'  WHERE name = 'HYBE';
UPDATE stocks SET short_name = '만도'    WHERE name = 'HL만도';

-- 적용 확인
-- SELECT name, short_name FROM stocks WHERE is_target = TRUE ORDER BY name;
