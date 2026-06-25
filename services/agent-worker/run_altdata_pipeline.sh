#!/bin/sh
# AltData 파이프라인 1회 실행: 수집 → 정규화 → 분석 → final_signals.
#
# Cloud Run Job(+Cloud Scheduler)에서 `sh run_altdata_pipeline.sh`로 호출한다.
# DB는 DATABASE_URL(env/secret)로, 수집 API 키는 env로 주입된다(이미지에 비밀 없음).
#
# 수집은 외부 API 의존(특허=BigQuery/KIPRIS, DataLab=Naver)이라 일부 소스의 키가 없거나
# 쿼터 소진이어도 정규화/분석이 기존 큐를 비우도록 진행한다(GitHub 워크플로 continue-on-error와 동일).
# 정규화/분석 실패는 그대로 비정상 종료(Job이 실패로 표시 → 재시도/알림).

echo "[pipeline] collect (datalab)…"
python run_collectors.py --datalab-only || echo "[pipeline] datalab collect failed — continuing"

echo "[pipeline] collect (patent)…"
python run_collectors.py --patent-only  || echo "[pipeline] patent collect failed — continuing"

echo "[pipeline] normalize…"
python run_normalizers.py

echo "[pipeline] analyze → final_signals…"
python run_analyzers.py

echo "[pipeline] done."
