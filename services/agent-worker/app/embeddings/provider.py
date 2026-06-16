from __future__ import annotations

import asyncio
from typing import Protocol

# BGE-M3 임베딩 차원. report_chunks.embedding VECTOR(1024)와 반드시 일치해야 한다.
EMBEDDING_DIM = 1024
DEFAULT_MODEL_NAME = "BAAI/bge-m3"


class EmbeddingProvider(Protocol):
    """임베딩 백엔드 추상화.

    적재(문서 청크)와 검색(질의)이 **동일 provider·정규화**를 써야 코사인 거리가 유의미하다.
    추후 외부 서비스(HF TEI 등)로 전환 시 이 Protocol 구현 1개만 교체한다.
    """

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalBGEM3Provider:
    """in-process BGE-M3 (1024-dim). MVP 기본 구현.

    모델은 무거우므로(~2GB) 모듈 싱글톤으로 1회만 로딩한다(get_embedding_provider).
    """

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME) -> None:
        # import를 메서드 안에 두어, 모델/torch가 필요 없는 경로에서는 로딩 비용을 피한다.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # model.encode()는 동기·CPU(또는 GPU) 점유 → to_thread로 오프로드해
        # 이벤트 루프 블로킹을 방지한다(async 정직성).
        return await asyncio.to_thread(self._encode, texts)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        # normalize_embeddings=True → 단위벡터. 검색 시 코사인 거리(<=>)와 정합.
        return self._model.encode(texts, normalize_embeddings=True).tolist()


_PROVIDER: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    """프로세스당 1개의 임베딩 provider 싱글톤을 돌려준다.

    적재(embed_report 핸들러)와 검색(rag_retriever)이 이 동일 인스턴스를 공유한다.
    """
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = LocalBGEM3Provider()
    return _PROVIDER


def set_embedding_provider(provider: EmbeddingProvider | None) -> None:
    """싱글톤을 주입/초기화한다(테스트·외부 provider 전환용)."""
    global _PROVIDER
    _PROVIDER = provider


def to_pgvector(embedding: list[float]) -> str:
    """임베딩을 pgvector 입력 문자열 '[0.1,0.2,...]' 로 직렬화한다.

    asyncpg 풀에는 pgvector 코덱이 등록돼 있지 않으므로(data-access create_pool 참고),
    SQL에서 `$n::vector` 로 캐스팅할 수 있는 문자열로 만든다. 적재(embed_report)와
    검색(rag_retriever) 양쪽이 동일 포맷을 쓴다.
    """
    return "[" + ",".join(str(float(x)) for x in embedding) + "]"
