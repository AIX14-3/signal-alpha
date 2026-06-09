from __future__ import annotations

from typing import Any, Protocol

from app.collectors.dart.corp_codes import DartCorpCodeClient, DartCorpCodeEntry


class DartCorpCodeRepository(Protocol):
    async def upsert_listed_corp_codes(self, entries: list[dict[str, Any]]) -> int:
        pass


class DartCorpCodeSyncService:
    def __init__(
        self,
        *,
        client: DartCorpCodeClient,
        repository: DartCorpCodeRepository,
    ) -> None:
        self._client = client
        self._repository = repository

    async def sync(self) -> dict[str, int]:
        entries = await self._client.fetch_corp_codes()
        listed_entries = [_to_repository_entry(entry) for entry in entries if entry.stock_code]
        upserted_count = await self._repository.upsert_listed_corp_codes(listed_entries)
        return {
            "fetched_count": len(entries),
            "listed_count": len(listed_entries),
            "upserted_count": upserted_count,
        }


def _to_repository_entry(entry: DartCorpCodeEntry) -> dict[str, str]:
    return {
        "corp_code": entry.corp_code,
        "ticker": entry.stock_code,
        "corp_name": entry.corp_name,
        "stock_name": entry.corp_name,
    }
