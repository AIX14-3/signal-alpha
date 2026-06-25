#!/usr/bin/env bash
# One-shot environment setup for the DataLab ML backfill on a fresh server
# (e.g. a rented vast.ai Ubuntu box). Installs uv, the agent-worker package with
# its workspace dep (signal-alpha-data-access), and the ml + backfill extras.
#
# Run from the REPO ROOT:
#   bash services/agent-worker/scripts/setup_remote.sh
set -euo pipefail

echo "==> Python:"; python3 --version || { echo "python3 missing"; exit 1; }

# 1) uv (fast installer/resolver the repo is built around)
if ! command -v uv >/dev/null 2>&1; then
  echo "==> installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv --version

# 2) sync the agent-worker package + workspace deps + ml/backfill extras
#    (creates .venv at the repo root)
echo "==> uv sync (agent-worker + ml + backfill)"
uv sync --package signal-alpha-agent-worker --extra ml --extra backfill

# 3) optional gradient boosters (xgboost/lightgbm/catboost); harness skips if absent
if [ "${WITH_BOOSTERS:-0}" = "1" ]; then
  echo "==> installing optional boosters"
  uv sync --package signal-alpha-agent-worker --extra ml --extra backfill --extra ml-boost
fi

echo
echo "==> done. Next:"
echo "   export DATABASE_URL=...  NAVER_CLIENT_ID=...  NAVER_CLIENT_SECRET=..."
echo "   cd services/agent-worker"
echo "   uv run python scripts/backfill_prices_fdr.py --start 2021-01-01 --end 2023-12-31 --out prices_2021_2023.csv"
echo "   uv run python scripts/backfill_datalab.py --start-year 2021 --end-year 2023"
echo "   uv run python -m app.ml.research.bakeoff --source datalab-db --prices-csv prices_2021_2023.csv --benchmark KS11"
