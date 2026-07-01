"""pgvector literal encoding for asyncpg.

asyncpg has no built-in codec for the pgvector ``vector`` type, and the project
does not depend on the ``pgvector`` python package. Rather than register a codec,
we bind the embedding as a text literal and cast it in SQL (``$N::vector``) — the
same explicit ``$N::TYPE`` habit the repositories already use for arrays/JSONB.

The dimension (768) is validated upstream by ``GeminiEmbeddingClient._validate``
before a vector ever reaches here, so this helper only formats.
"""
from __future__ import annotations

from collections.abc import Sequence


def to_pgvector(vec: Sequence[float]) -> str:
    """Format a float sequence as a pgvector literal, e.g. ``[0.1,0.2,...]``.

    Pair with a ``$N::vector`` cast in the query. ``repr(float(x))`` keeps full
    round-trip precision without locale/format surprises.
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"
