# run_key 운영 기준

`run_key`는 같은 날짜 안에서 분석 실행 단위를 구분합니다.

| run_key | 실행 상황 |
| --- | --- |
| `AM` | 오전 리포트 반영 정기 분석 |
| `PM` | 오후 리포트 반영 정기 분석 |
| `BATCH_NIGHT` | 야간 배치 분석 |
| `IMMEDIATE` | DART 고임팩트 즉시 분석 |
| `MANUAL` | 수동 재분석 |

DART 즉시 분석이 하루에 여러 번 발생할 가능성이 있으면 `IMMEDIATE_1030`, `IMMEDIATE_1430`처럼 시간 기반으로 세분화할 수 있습니다.

## final_signals 기준

- `final_signals`는 `stock_id + signal_date + run_key` 기준으로 `is_current = TRUE`인 현재 대표 시그널을 1개만 유지합니다.
- `AM`과 `PM`은 서로 다른 `run_key`이므로 같은 날짜에 각각 대표 시그널을 가질 수 있습니다.

## 조회 예시 SQL

오전 대표 시그널 조회:

```sql
SELECT *
FROM final_signals
WHERE stock_id = :stock_id
  AND signal_date = CURRENT_DATE
  AND run_key = 'AM'
  AND is_current = TRUE;
```

오후 대표 시그널 조회:

```sql
SELECT *
FROM final_signals
WHERE stock_id = :stock_id
  AND signal_date = CURRENT_DATE
  AND run_key = 'PM'
  AND is_current = TRUE;
```

오늘 최신 대표 시그널 1개 조회:

```sql
SELECT *
FROM final_signals
WHERE stock_id = :stock_id
  AND signal_date = CURRENT_DATE
  AND is_current = TRUE
ORDER BY published_at DESC NULLS LAST, created_at DESC
LIMIT 1;
```
