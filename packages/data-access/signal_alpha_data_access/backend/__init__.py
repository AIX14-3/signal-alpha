"""backend(main-server) 전용 data-access facade.

worker/backend 의존성 분리(Model A): backend 는 worker 전용 repository(collection,
normalization, scoring, dart_* 등)에 의존하지 않는다. main-server 코드는 반드시
`from signal_alpha_data_access.backend import ...` 로만 repository 를 가져온다
(CI 가드가 main-server 의 .worker / .repositories import 를 차단).

구성:
- 읽기 계약(api.* view): StockRepository, ProcessingQueueRepository  → read_contract
- worker 산출물 읽기(api.* view): SignalRepository, UserSignalRepository → repositories.*
  (두 클래스는 backend 전용이라 base→api 로 직접 수정됨)
- backend 소유 테이블 DML: UserBillingRepository, UserSessionRepository,
  AdminRepository
"""

from signal_alpha_data_access.backend.read_contract import (
    ProcessingQueueRepository,
    StockRepository,
)
from signal_alpha_data_access.repositories.admin import AdminRepository
from signal_alpha_data_access.repositories.backtests import BacktestRepository
from signal_alpha_data_access.repositories.collection_schedules import (
    CollectionScheduleRepository,
    parse_schedule_row,
)
from signal_alpha_data_access.repositories.signals import SignalRepository
from signal_alpha_data_access.repositories.user_sessions import UserSessionRepository
from signal_alpha_data_access.repositories.user_signals import UserSignalRepository
from signal_alpha_data_access.repositories.users_billing import UserBillingRepository

__all__ = [
    "AdminRepository",
    "BacktestRepository",
    "CollectionScheduleRepository",
    "ProcessingQueueRepository",
    "SignalRepository",
    "StockRepository",
    "UserBillingRepository",
    "UserSessionRepository",
    "UserSignalRepository",
    "parse_schedule_row",
]
