from __future__ import annotations

from typing import Any

from signal_alpha_data_access.repositories.raw_details._common import _jsonb


class _DatalabRawMixin:
    """네이버 DataLab raw detail — category↔stock 매핑·정규화용 조회·upsert.

    DataLab 은 category 단위로 수집되어 detail 행이 ``category_id`` 를 갖는다(stock_id 아님).
    앵커 테이블도 ``datalab_raw_documents`` 로 다른 소스와 다르다.
    """

    _connection: Any  # _RawDetailBase 가 __init__ 에서 주입

    async def list_stocks_for_datalab_category(self, category_id: int) -> list[Any]:
        """Active stock_ids mapped to a DataLab category (datalab_category_stocks).

        DataLab raw has no ``raw_documents``/``stock_id`` anchor, so the route
        handler resolves category → stock here to enqueue per-stock analysis.
        """
        return await self._connection.fetch(
            """
            SELECT dcs.stock_id, dcs.weight
            FROM datalab_category_stocks dcs
            INNER JOIN stocks s ON s.id = dcs.stock_id
            WHERE dcs.category_id = $1
              AND s.is_active = TRUE
            ORDER BY dcs.stock_id
            """,
            category_id,
        )

    async def list_datalab_categories_for_stock(self, stock_id: int) -> list[Any]:
        """(category_id, weight) rows mapping this stock to its DataLab themes."""
        return await self._connection.fetch(
            """
            SELECT
                dcs.category_id,
                dcs.weight,
                dc.name AS category_name
            FROM datalab_category_stocks dcs
            INNER JOIN datalab_categories dc ON dc.id = dcs.category_id
            WHERE dcs.stock_id = $1
              AND dc.is_active = TRUE
            ORDER BY dcs.category_id
            """,
            stock_id,
        )

    async def list_datalab_details_by_category(
        self,
        *,
        category_ids: list[int],
        since_date: Any | None = None,
    ) -> list[Any]:
        """DataLab detail rows for the given categories within the window.

        DataLab is collected by category, so detail rows carry ``category_id``
        (not stock_id) — see migration 020.
        """
        if not category_ids:
            return []

        return await self._connection.fetch(
            """
            SELECT
                d.raw_document_id,
                d.category_id,
                d.keyword,
                d.keyword_group,
                d.observed_date,
                d.search_index,
                d.previous_search_index,
                d.change_pct,
                d.period_type,
                d.device,
                d.gender,
                d.age_group,
                d.is_spike,
                d.extra_payload,
                COALESCE(dck.polarity, 'demand') AS polarity,
                COALESCE(dck.polarity_source, 'default') AS polarity_source,
                dck.polarity_model AS polarity_model
            FROM datalab_raw_details d
            LEFT JOIN datalab_category_keywords dck
                ON dck.category_id = d.category_id AND dck.keyword = d.keyword
            WHERE d.category_id = ANY($1::BIGINT[])
              AND ($2::date IS NULL OR d.observed_date >= $2)
            ORDER BY d.observed_date DESC, d.raw_document_id DESC
            """,
            category_ids,
            since_date,
        )

    async def list_datalab_details_by_raw_ids(
        self, raw_document_ids: list[int]
    ) -> list[Any]:
        """DataLab detail rows + their datalab_raw_documents meta, by raw id.

        Mirrors ``list_hiring_details_by_raw_ids``/``list_patent_details_by_raw_ids``
        for the Normalizer: the NORMALIZE_DATALAB handler reads the datalab raw the
        collector enqueued (``source_raw_ids`` = ``datalab_raw_documents.id``) and
        converts each observation into source_documents/signal_events per mapped
        stock. ``polarity`` (demand|risk) is joined from datalab_category_keywords
        so per-event direction is polarity-aware. Note the anchor table is
        ``datalab_raw_documents`` (NOT ``raw_documents``).
        """
        if not raw_document_ids:
            return []

        return await self._connection.fetch(
            """
            SELECT
                d.raw_document_id,
                d.category_id,
                d.keyword,
                d.keyword_group,
                d.observed_date,
                d.search_index,
                d.previous_search_index,
                d.change_pct,
                d.period_type,
                d.device,
                d.gender,
                d.age_group,
                d.is_spike,
                d.extra_payload,
                COALESCE(dck.polarity, 'demand') AS polarity,
                doc.source_name,
                doc.title,
                doc.source_url,
                doc.published_at,
                doc.collected_at
            FROM datalab_raw_details d
            INNER JOIN datalab_raw_documents doc
                ON doc.id = d.raw_document_id
            LEFT JOIN datalab_category_keywords dck
                ON dck.category_id = d.category_id AND dck.keyword = d.keyword
            WHERE d.raw_document_id = ANY($1::BIGINT[])
            ORDER BY d.observed_date DESC, d.raw_document_id DESC
            """,
            raw_document_ids,
        )

    async def upsert_datalab_detail(
        self,
        *,
        raw_document_id: int,
        stock_id: int,
        keyword: str,
        observed_date: Any,
        search_index: Any,
        keyword_group: str | None = None,
        previous_search_index: Any | None = None,
        change_pct: Any | None = None,
        period_type: str = "daily",
        device: str = "all",
        gender: str = "all",
        age_group: str = "all",
        is_spike: bool = False,
        extra_payload: Any | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO datalab_raw_details (
                raw_document_id, stock_id, keyword, keyword_group, observed_date,
                search_index, previous_search_index, change_pct, period_type,
                device, gender, age_group, is_spike, extra_payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            ON CONFLICT (stock_id, keyword, observed_date, period_type, device, gender, age_group)
            DO UPDATE SET
                raw_document_id = EXCLUDED.raw_document_id,
                keyword_group = EXCLUDED.keyword_group,
                search_index = EXCLUDED.search_index,
                previous_search_index = EXCLUDED.previous_search_index,
                change_pct = EXCLUDED.change_pct,
                is_spike = EXCLUDED.is_spike,
                extra_payload = EXCLUDED.extra_payload
            RETURNING *
            """,
            raw_document_id,
            stock_id,
            keyword,
            keyword_group,
            observed_date,
            search_index,
            previous_search_index,
            change_pct,
            period_type,
            device,
            gender,
            age_group,
            is_spike,
            _jsonb(extra_payload),
        )
