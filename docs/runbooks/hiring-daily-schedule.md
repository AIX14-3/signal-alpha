# 런북 — 일별 채용 수집·분석 자동 스케줄링 (#297)

수집·분석 파이프라인을 **서버 cron**으로 매일 1회 자동 실행한다. 이게 돌아야 `hiring_raw_details`
일별 이력이 쌓여 분석기 baseline이 성숙(Phase A)하고 신호 신뢰도·parity 측정이 가능해진다.

> 산출물: `services/agent-worker/ops/` 의 `run_hiring_daily.sh` · `hiring-daily.crontab` · `logrotate-hiring`.
> 이 절차는 **리눅스 서버**에서 1회 수행한다(개발 머신 아님).

## 무엇이 도는가
`run_hiring_daily.sh` 한 스크립트가:
1. **flock**으로 중복 기동 차단(이미 돌고 있으면 조용히 종료, 알림 없음).
2. repo `.env`를 자동 로드(셸·파이썬 공용 env).
3. 체인 실행 — `run_daily_hiring_pipeline.py`(수집 + 레거시 분석 → `hiring_signals`) **&&**
   `run_analyzers.py`(신규 분석 → `final_signals`). **수집 실패 시 분석은 건너뜀.**
4. 날짜별 로그 적재(`/var/log/hiring/YYYY-MM-DD.log`, `.analyze.log`).
5. exit≠0이면 **Discord 알림**(침묵 실패 방지).

멱등: `run_daily`=`ON CONFLICT`, `run_analyzers`=`dedupe(as_of)` → 같은 날 재실행해도 안전.

## 사전 조건
- `uv` 설치(기본 `~/.local/bin/uv`). `which uv`로 경로 확인 — 다르면 `run_hiring_daily.sh`의 `export PATH=...` 줄 조정.
- Chrome + ChromeDriver(WebDriver Manager 자동) — 수집이 Selenium 사용.
- repo `.env`에 `DATABASE_URL`, `HIRING_DATALAB_CLIENT_ID/SECRET`, (선택) `DISCORD_WEBHOOK_URL` 설정.
  `.env` 경로가 repo 루트가 아니면 cron 라인 앞에 `ENV_FILE=/path/.env` 지정.

## 설치 절차
아래에서 `<repo>`=체크아웃 경로(예: `/home/ubuntu/signal-alpha`), `<user>/<group>`=서비스 계정(예: `ubuntu ubuntu`).

### 1) 로그 디렉터리 + 소유권
```bash
sudo mkdir -p /var/log/hiring
sudo chown <user>:<group> /var/log/hiring
```

### 2) 래퍼 실행권한
```bash
chmod +x <repo>/services/agent-worker/ops/run_hiring_daily.sh
```

### 3) 수동 1회 검증 (cron 등록 전)
```bash
# 로컬 로그 디렉터리로 안전하게 먼저 시험 가능
HIRING_LOG_DIR=/tmp/hiring-test <repo>/services/agent-worker/ops/run_hiring_daily.sh
ls -l /tmp/hiring-test/        # YYYY-MM-DD.log / .analyze.log 생성 확인
psql "$DATABASE_URL" -c "SELECT count(*) FROM final_signals;"   # 적재 확인
# 중복 기동 차단 확인: 위 명령을 두 개 터미널에서 동시 실행 → 한쪽은 'skip' 후 즉시 종료(exit 0)
```

### 4) crontab 등록
`ops/hiring-daily.crontab` 참고. 사용자 crontab:
```bash
crontab -e
# 아래 두 줄 추가(<repo> 치환):
CRON_TZ=Asia/Seoul
0 6 * * *  <repo>/services/agent-worker/ops/run_hiring_daily.sh
```
- **CRON_TZ 미지원(구형 cron)**: 위 두 줄 대신 `0 21 * * * <repo>/.../run_hiring_daily.sh` (UTC 21:00 = KST 06:00).
- 06:00 KST인 이유: `observed_date`가 KST 자정 기준(#253) → 자정 이후에 돌려야 당일분이 잡힘.

### 5) logrotate 등록
```bash
sudo cp <repo>/services/agent-worker/ops/logrotate-hiring /etc/logrotate.d/hiring
sudo sed -i 's/<user> <group>/<user> <group>/' /etc/logrotate.d/hiring   # 실제 계정으로 치환
sudo logrotate -d /etc/logrotate.d/hiring     # 문법 검증(dry-run)
```
- `su`/`create`에 실제 계정을 넣어야 비루트 로그 디렉터리에서 회전·권한 에러가 안 난다.

## 완료 기준 (검증)
- [ ] 다음날 06:00(KST) 자동 1회 실행 → `final_signals` 당일 `signal_date` 적재.
- [ ] 날짜별 로그 생성(`/var/log/hiring/`), flock 중복 기동 차단 동작.
- [ ] 강제 실패 주입 시 Discord 알림 도착(예: `.env`의 `DATABASE_URL`을 잘못 줘서 1회 실행 → exit≠0 트랩).
- [ ] 며칠 뒤 logrotate로 `.gz` 회전 + 새 로그 권한 정상.

## 알림 역할 분담 — ops_daemon vs cron-trap (중복 아님)
| 구분 | `observability/ops_daemon.py` (상시) | cron-trap (`run_hiring_daily.sh`, 일별) |
|---|---|---|
| 실행 형태 | 데몬/상시 구동 | cron 일별 배치(06:00 KST) |
| 감지 대상 | **collector_runs에 기록된** 임계(거부율·전건 실패 등) | **run이 시작도 못 하거나 크래시**해 row조차 없는 침묵 실패 |
| 대응 | 임계 알림(상시 헬스체크) | exit≠0 즉시 Discord 알림 |
| 리소스 | 지속 점유 | 실행 시점에만(Selenium) 일시 점유 |

→ 둘은 **상호 보완**: 데몬은 "돌았지만 품질 나쁨", cron-trap은 "아예 못 돎"을 각각 커버.

## 컷오버 메모 (#188)
현재 체인은 **레거시(hiring_signals) + 신규(final_signals) 둘 다** 적재한다(이행기 parity 비교용).
parity 통과·컷오버(#188 C0~C5) 확정 후 `run_daily`의 레거시 분석(Step2)을 제거하고 신규만 남긴다.
