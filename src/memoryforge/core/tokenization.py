"""Shared deterministic tokenization for FTS indexing and lexical retrieval.

Two behaviors live here on purpose:

- :func:`index_terms` mirrors the historical ``_search_terms`` semantics used by
  the ``source_fts.search_terms`` column: each CJK run decomposes into
  **unigrams and bigrams** (``extend(run)`` iterates the characters), Latin/digit
  runs are lowercased. Byte-identical to the previous inline implementation
  (locked by golden tests).
- :func:`bigram_tokens` mirrors the historical ``retrieval_v2._tokenize``
  semantics for in-memory TF-IDF: CJK runs expand to bigrams only.

Both write and query sides must import from this module so the two can never
drift apart again (spec: PROGRESSIVE_STRUCTURE_RETRIEVAL_SPEC §4).
"""

from __future__ import annotations

import re

# Broad CJK coverage (incl. Ext-A and compatibility ideographs) — used by the
# FTS search-terms contract.
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_SEARCH_RUN = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")

# Narrow range kept for retrieval TF-IDF parity with the previous behavior.
_RETRIEVAL_WORD = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_RETRIEVAL_CJK = re.compile(r"^[\u4e00-\u9fff]+$")


def index_terms(text: str) -> list[str]:
    """Return FTS index terms: CJK unigrams + bigrams + lowercased Latin runs."""
    terms: list[str] = []
    for match in _SEARCH_RUN.finditer(text):
        run = match.group(0)
        if _CJK_RUN.fullmatch(run):
            terms.extend(run)
            if len(run) > 1:
                terms.extend(run[index : index + 2] for index in range(len(run) - 1))
        else:
            terms.append(run.lower())
    return terms


def index_terms_text(text: str) -> str:
    """Space-joined :func:`index_terms` output, matching the stored column format."""
    return " ".join(index_terms(text))


def bigram_tokens(text: str) -> list[str]:
    """Return retrieval tokens: CJK runs become bigrams, Latin/digit runs lowercase."""
    tokens: list[str] = []
    for match in _RETRIEVAL_WORD.finditer(text.lower()):
        token = match.group(0)
        if _RETRIEVAL_CJK.fullmatch(token) and len(token) > 1:
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
        else:
            tokens.append(token)
    return tokens
