"""공통 코어: DB 풀/설정. worker·backend 양쪽이 의존한다.

worker/backend 분리(Model A: 단일 Postgres + 소유권 경계)의 공통 레이어.
실제 구현은 패키지 루트 database.py 에 있으며 여기서 안정적인 import 경로
(`signal_alpha_data_access.core`)로 재노출한다.
"""

from signal_alpha_data_access.database import DatabaseSettings, create_pool

__all__ = ["DatabaseSettings", "create_pool"]
