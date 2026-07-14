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

echo "[1/4] 클러스터 Workload Identity 풀 활성화 (노드 무영향, 수 분)"
gcloud container clusters update "$CLUSTER" --zone "$ZONE" \
  --workload-pool="${PROJECT}.svc.id.goog"

echo "[2/4] 노드풀 3개 GKE_METADATA 전환 (⚠️풀별 노드 롤링 재생성)"
for POOL in default-pool small-pool private-pool; do
  echo "  - $POOL ..."
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
