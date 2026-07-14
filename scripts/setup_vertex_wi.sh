#!/usr/bin/env bash
# Vertex AI Workload Identity 셋업 + LLM 채점 HIRING 1소스 ON (일회성 운영 스크립트).
#
# 배경: 노드 SA 는 cloud-platform 스코프가 없고, org 정책
# (iam.disableServiceAccountKeyCreation)이 SA 키 발급을 금지 → WI 가 유일한 인증 경로.
# GSA `vertex-llm`(aiplatform.user 부여됨)·KSA `vertex-llm`(deploy/k8s 매니페스트)은 준비됨.
#
# ⚠️ [2/4] 노드풀 GKE_METADATA 전환은 노드 롤링 재생성을 유발한다(풀당 수 분,
#    서비스 순간 영향 가능). 한가한 시간대에 실행 권장.
#
# ⚠️ 서지 업그레이드 금지: 지역 SSD 쿼터가 205/250GB 라 서지 노드(+100GB 부트디스크)
#    생성이 QUOTA_EXCEEDED 로 실패한다(2026-07-14 실측). maxSurge=0·maxUnavailable=1 로
#    추가 노드 없이 한 대씩 교체한다 — 교체 중 노드 1대 분량 용량이 잠시 빠진다.
#
# 실행:  bash scripts/setup_vertex_wi.sh
# 롤백(플래그만):
#   kubectl patch secret signal-alpha-secrets -n signal-alpha --type merge \
#     -p '{"stringData":{"LLM_SCORING_ENABLED":"false"}}' \
#   && kubectl rollout restart deployment agent-worker -n signal-alpha
set -euo pipefail

PROJECT=signal-alpha-demo
ZONE=asia-northeast3-a
CLUSTER=sa-gke
GSA=vertex-llm@${PROJECT}.iam.gserviceaccount.com

echo "[1/4] 클러스터 Workload Identity 풀 활성화 (노드 무영향, 수 분 — 이미 켜져 있으면 즉시 통과)"
CURRENT_POOL=$(gcloud container clusters describe "$CLUSTER" --zone "$ZONE" \
  --format='value(workloadIdentityConfig.workloadPool)')
if [ "$CURRENT_POOL" = "${PROJECT}.svc.id.goog" ]; then
  echo "  이미 활성화됨 — skip"
else
  gcloud container clusters update "$CLUSTER" --zone "$ZONE" \
    --workload-pool="${PROJECT}.svc.id.goog"
fi

echo "[2/4] 노드풀 3개 GKE_METADATA 전환 (⚠️풀별 노드 롤링 재생성·서지 없이 1대씩)"
for POOL in default-pool small-pool private-pool; do
  MODE=$(gcloud container node-pools describe "$POOL" --cluster "$CLUSTER" --zone "$ZONE" \
    --format='value(config.workloadMetadataConfig.mode)')
  if [ "$MODE" = "GKE_METADATA" ]; then
    echo "  - $POOL 이미 전환됨 — skip"
    continue
  fi
  echo "  - $POOL: 서지 0 설정(SSD 쿼터 회피)"
  gcloud container node-pools update "$POOL" --cluster "$CLUSTER" --zone "$ZONE" \
    --max-surge-upgrade=0 --max-unavailable-upgrade=1
  echo "  - $POOL: GKE_METADATA 롤링 (한 대씩 교체)"
  gcloud container node-pools update "$POOL" --cluster "$CLUSTER" --zone "$ZONE" \
    --workload-metadata=GKE_METADATA
done

echo "[3/4] KSA(signal-alpha/vertex-llm) → GSA 바인딩"
gcloud iam service-accounts add-iam-policy-binding "$GSA" \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:${PROJECT}.svc.id.goog[signal-alpha/vertex-llm]" >/dev/null
echo "  ok"

echo "[4/4] LLM 채점 플래그 ON — HIRING 1소스 점진, 폴백 rules + 워커 재기동"
kubectl patch secret signal-alpha-secrets -n signal-alpha --type merge -p '{
  "stringData": {
    "LLM_SCORING_ENABLED": "true",
    "LLM_SCORING_SOURCES": "HIRING",
    "LLM_SCORING_FALLBACK": "rules"
  }
}'
kubectl rollout restart deployment agent-worker -n signal-alpha
kubectl rollout status deployment agent-worker -n signal-alpha --timeout=300s

echo "완료. 드레인 데몬이 SCORE_COHORT 를 일 1회 시드한다. 확인:"
echo "  kubectl logs deployment/agent-worker -n signal-alpha --tail 100 | grep -i cohort"
