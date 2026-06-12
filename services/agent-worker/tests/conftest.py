import os

# 로컬 .env에 DATABASE_URL이 있으면 TestClient의 lifespan이 price 수집 데몬을
# 실제로 띄워 버린다. 테스트에서는 데몬을 명시적으로만 다루도록 기본 차단한다.
# (config.py의 load_dotenv는 기존 env를 덮지 않으므로 여기서 먼저 고정한다.)
os.environ.setdefault("PRICE_COLLECTOR_ENABLED", "false")
