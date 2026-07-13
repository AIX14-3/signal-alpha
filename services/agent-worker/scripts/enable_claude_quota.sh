#!/usr/bin/env bash
# Vertex AI 에서 Claude 를 쓰기 위한 할당량 증액.
#
# 배경 (실측으로 확인한 것):
#   - Model Garden 활성화/약관 동의는 **이미 돼 있다**. 별도 조치 불필요.
#     (증거: us-east5 는 404 "model not found" 인데 global 은 429 "Quota exceeded" 를 낸다.
#      429 가 나온다는 건 모델엔 접근되는데 호출 한도가 0 이라는 뜻.)
#   - Claude 는 이 프로젝트에서 **global 리전 전용**으로 서빙된다.
#   - 유일한 병목은 **할당량 0**.
#
# 이 스크립트가 하는 일: Claude Haiku 4.5 의 분당 호출 한도를 0 → 30 으로 올린다.
#   - 그 자체로 과금되지 않는다(실제 호출해야 과금).
#   - 되돌리려면 VALUE=0 으로 다시 실행.
#   - 우리 A/B 는 하루 7~49콜이라 30 QPM 이면 충분하다.
#
# 사용:
#   bash services/agent-worker/scripts/enable_claude_quota.sh            # haiku 4.5, 30 QPM
#   VALUE=0 bash services/agent-worker/scripts/enable_claude_quota.sh    # 되돌리기
#   MODEL=anthropic-claude-sonnet-4-5 bash .../enable_claude_quota.sh    # 다른 모델
set -euo pipefail

PROJECT="${PROJECT:-signal-alpha-demo}"
MODEL="${MODEL:-anthropic-claude-haiku-4-5}"
VALUE="${VALUE:-30}"
METRIC="aiplatform.googleapis.com/global_online_prediction_requests_per_base_model"
# unit 은 '1' 이 아니라 이 문자열이어야 한다(SU_INVALID_UNIT: "quota unit must start with '1/'").
# `gcloud alpha services quota list ... --format=json` 의 consumerQuotaLimits[].unit 에서 확인.
UNIT="1/min/{project}/{base_model}"

echo "프로젝트 : ${PROJECT}"
echo "모델     : ${MODEL}"
echo "할당량   : ${VALUE} (분당 호출)"
echo

gcloud alpha services quota update \
  --consumer="projects/${PROJECT}" \
  --service=aiplatform.googleapis.com \
  --metric="${METRIC}" \
  --unit="${UNIT}" \
  --dimensions="base_model=${MODEL}" \
  --value="${VALUE}" \
  --force

echo
echo "완료. 아래로 검증:"
echo "  python services/agent-worker/scripts/probe_claude_vertex.py"
