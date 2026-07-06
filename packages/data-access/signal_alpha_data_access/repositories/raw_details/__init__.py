"""raw_details 리포지토리 — 소스별 mixin 조립.

``RawDetailRepository`` 는 dart/hiring/patent/report/datalab mixin 을 상속만 하며,
public 인터페이스·메서드 시그니처는 단일 파일 시절과 100% 동일하다. 기존
``from signal_alpha_data_access.repositories.raw_details import RawDetailRepository``
경로가 그대로 유지된다(module → package).
"""

from __future__ import annotations

from signal_alpha_data_access.repositories.raw_details._common import _RawDetailBase
from signal_alpha_data_access.repositories.raw_details.dart import _DartRawMixin
from signal_alpha_data_access.repositories.raw_details.datalab import _DatalabRawMixin
from signal_alpha_data_access.repositories.raw_details.hiring import _HiringRawMixin
from signal_alpha_data_access.repositories.raw_details.patent import _PatentRawMixin
from signal_alpha_data_access.repositories.raw_details.report import _ReportRawMixin


class RawDetailRepository(
    _DartRawMixin,
    _HiringRawMixin,
    _PatentRawMixin,
    _ReportRawMixin,
    _DatalabRawMixin,
    _RawDetailBase,
):
    """수집 raw detail(dart/hiring/patent/report/datalab) 통합 리포지토리."""


__all__ = ["RawDetailRepository"]
