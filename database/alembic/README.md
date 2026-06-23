# DB 마이그레이션 — Alembic (ORM 기반, 하이브리드)

스키마 변경(추가/수정/삭제)은 이제 **SQLAlchemy 모델을 고치고 Alembic 으로 마이그레이션을
자동 생성**하는 흐름으로 관리한다.

- 모델(진실원천): `packages/data-access/signal_alpha_data_access/models/`
- 마이그레이션: `database/alembic/versions/`
- 설정: 루트 `alembic.ini`, 환경: `database/alembic/env.py`

## 레거시와의 관계 (하이브리드)
- `database/migrations/001~023.sql` + `database/migrate.py` 는 **동결**되어 *베이스라인*을 만든다.
  더 이상 새 `NNN_*.sql` 을 추가하지 않는다.
- 베이스라인 리비전: `0001_baseline_after_023`(no-op). 그 이후 변경부터 Alembic 이 관리한다.

## 설치
```bash
uv sync --all-packages         # alembic·sqlalchemy·pgvector 포함(루트 dev + data-access)
```

## 부트스트랩 (최초 1회)
```bash
# 이미 023 까지 적용된 기존 DB:
alembic stamp 0001_baseline_after_023

# 새 DB(처음부터):
python database/migrate.py apply
alembic stamp 0001_baseline_after_023
alembic upgrade head
```

## 일상 워크플로 — 추가 / 수정 / 삭제
1. `models/` 에서 모델을 **추가/수정/삭제**한다.
   - 새 모델 파일을 만들면 **반드시 `models/__init__.py` 에서 import** (그래야 autogenerate 가 봄).
2. 마이그레이션 자동 생성:
   ```bash
   alembic revision --autogenerate -m "add fx_rates.source"
   ```
3. **생성된 파일을 반드시 검토**한다. autogenerate 는 만능이 아니다 — 확인 포인트:
   - 트리거 / `ivfflat`(pgvector) 인덱스 / 표현식·부분 인덱스 / 일부 CHECK 는 누락될 수 있다
     → `op.execute("...")` 로 수기 보완.
   - 의도치 않은 DROP/RENAME 이 보이면(서버 기본값·제약 이름 차이 등) 모델을 실제에 맞춰 정렬.
4. 적용 / 롤백:
   ```bash
   alembic upgrade head
   alembic downgrade -1
   ```

## 브랜치 병렬 작업 (번호 충돌 없음)
- 리비전 ID 는 정수 순번이 아니라 해시이고 순서는 `down_revision` DAG 로 결정된다.
- 두 브랜치가 각자 리비전을 만들어 머지하면 head 가 둘이 된다 → 합치기:
  ```bash
  alembic heads          # 여러 head 확인
  alembic merge -m "merge heads" <rev_a> <rev_b>
  ```
- 파일명은 타임스탬프(`alembic.ini` file_template)라 파일 자체 충돌도 없다.

## pgvector 테이블
`dart_chunks`(embedding VECTOR(1024)) 같은 테이블을 모델로 옮길 때는
`pgvector.sqlalchemy.Vector(1024)` 를 쓰고, `ivfflat` 인덱스는 autogenerate 가 못 만드므로
마이그레이션에서 `op.execute("CREATE INDEX ... USING ivfflat (embedding vector_cosine_ops) WITH (lists=100)")`
로 직접 추가한다.

## 현재 모델링된 테이블 (점진 확장 중)
- `ml_inferences` (019), `meta_signals` (020) — 대표 예시.
- 나머지(stocks·raw_documents·dart_*·report_*·fx_rates·program_trading …)는 같은 패턴으로
  계속 모델을 추가한다. `env.py` 의 `include_name` 이 **모델 있는 테이블만** 건드리므로,
  아직 모델 없는 레거시 테이블은 안전하게 무시된다.
