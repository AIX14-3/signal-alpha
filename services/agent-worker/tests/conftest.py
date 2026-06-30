import os

# 로컬 .env에 DATABASE_URL이 있으면 TestClient의 lifespan이 price 수집 데몬을
# 실제로 띄워 버린다. 테스트에서는 데몬을 명시적으로만 다루도록 항상 차단한다.
# setdefault가 아닌 강제 할당 — 쉘에 export된 값도 테스트를 뚫으면 안 된다.
os.environ["PRICE_COLLECTOR_ENABLED"] = "false"
os.environ["INTERNAL_API_TOKEN"] = "test-internal-token"
