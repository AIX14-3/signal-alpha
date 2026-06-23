"""ML/메타러너 단계 테이블 모델 (대표 예시 — 점진 도입 시작점).

``ml_inferences``(019)·``meta_signals``(020)를 SQLAlchemy 모델로 정의한다. 이 두 테이블은
이미 레거시 .sql 로 DB에 존재하므로, 모델은 **실제 스키마와 일치하도록** 작성했다(제약/인덱스
이름·server_default 포함). 첫 ``alembic revision --autogenerate`` 는 이 테이블들에 대해
가급적 빈(no-op) diff 가 나야 한다 — 미세 차이(서버 기본값·표현식 인덱스 등)는 검토 후 모델을
DB에 맞추거나 그 반대로 정렬한다.

나머지 테이블은 같은 패턴으로 계속 추가하면 된다(추가/수정/삭제 → autogenerate).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from signal_alpha_data_access.models.base import Base


class MlInference(Base):
    """ml_inferences — 모델별 변동성 추론 결과(pred_vol). 자연키로 멱등 upsert."""

    __tablename__ = "ml_inferences"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stocks.id"), nullable=False)
    run_key: Mapped[str] = mapped_column(String(50), nullable=False, server_default="ML")
    asof_date: Mapped[date] = mapped_column(Date, nullable=False)
    model_name: Mapped[str] = mapped_column(String(50), nullable=False)
    horizon: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    pred_value: Mapped[float | None] = mapped_column(Double)  # 추론 실패 시 NULL
    device: Mapped[str] = mapped_column(String(10), nullable=False, server_default="cpu")
    gate_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "stock_id", "run_key", "asof_date", "model_name", "horizon", name="uq_ml_inference"
        ),
        CheckConstraint("device IN ('cpu', 'gpu')", name="ml_inferences_device_check"),
        Index("idx_ml_inferences_stock_asof", "stock_id", text("asof_date DESC")),
    )


class MetaSignal(Base):
    """meta_signals — 메타러너 stacking 결합(결합 변동성·신뢰도·가중). 자연키 멱등."""

    __tablename__ = "meta_signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stocks.id"), nullable=False)
    run_key: Mapped[str] = mapped_column(String(50), nullable=False, server_default="ML")
    asof_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    combined_vol: Mapped[float | None] = mapped_column(Double)  # 결합 불가 시 NULL
    confidence: Mapped[float] = mapped_column(Double, nullable=False, server_default=text("0"))
    method: Mapped[str] = mapped_column(String(20), nullable=False, server_default="stacking")
    model_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    weight_breakdown: Mapped[dict | None] = mapped_column(JSONB)  # {model_name: weight}
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("stock_id", "run_key", "asof_date", "horizon", name="uq_meta_signal"),
        CheckConstraint(
            "method IN ('stacking', 'equal_fallback', 'empty')", name="meta_signals_method_check"
        ),
        Index("idx_meta_signals_stock_asof", "stock_id", text("asof_date DESC")),
    )
