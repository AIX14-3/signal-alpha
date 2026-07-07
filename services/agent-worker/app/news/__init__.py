"""종목별 뉴스 수집(Naver News → stock_news) — display-only 컨텍스트 레이어.

guard 데몬 선례를 미러링해 워커가 BACKEND_DATABASE_URL 풀로 stock_news 를 적재하고,
main-server 가 api.stock_news 뷰로 읽는다. 시그널/점수 파이프라인과 무관.
"""
