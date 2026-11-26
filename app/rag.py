"""Поиск по руководствам: BM25 со стеммингом, строго в пределах руководств на двигатель ТС."""
import json
import re
from dataclasses import dataclass
from functools import lru_cache

import snowballstemmer
from rank_bm25 import BM25Okapi

from .config import INDEX_FILE
from .manuals import BY_ID, manuals_for_engine

_stem_ru = snowballstemmer.stemmer("russian")
_TOKEN = re.compile(r"[a-zа-яё]+|\d+(?:[.,]\d+)?", re.IGNORECASE)
_STOP = {
    "и", "в", "во", "на", "не", "с", "со", "по", "к", "у", "о", "об", "от", "до", "из", "за", "для", "при",
    "что", "как", "это", "а", "но", "или", "ли", "же", "то", "его", "ее", "их", "мы", "вы", "я", "он", "она",
    "какой", "какая", "какие", "каков", "можно", "нужно", "ли", "быть", "если", "чем", "где", "когда",
}


def tokenize(text: str) -> list[str]:
    toks = []
    for t in _TOKEN.findall(text.lower().replace("ё", "е")):
        if t in _STOP:
            continue
        toks.append(t if t[0].isdigit() else _stem_ru.stemWord(t))
    return toks


@dataclass
class Chunk:
    chunk_id: str
    manual_id: str
    pdf_page: int
    source: str
    text: str


class _Index:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.bm25 = BM25Okapi([tokenize(c.text) for c in chunks]) if chunks else None


@lru_cache(maxsize=1)
def _all_chunks() -> list[Chunk]:
    if not INDEX_FILE.exists():
        raise RuntimeError(f"Индекс не построен: {INDEX_FILE}. Запустите python -m scripts.build_index")
    with INDEX_FILE.open(encoding="utf-8") as f:
        return [Chunk(**json.loads(line)) for line in f]


@lru_cache(maxsize=16)
def _index_for(manual_ids: tuple[str, ...]) -> _Index:
    return _Index([c for c in _all_chunks() if c.manual_id in manual_ids])


def page_count(manual_id: str) -> int:
    return max((c.pdf_page for c in _all_chunks() if c.manual_id == manual_id), default=0)


def search(query: str, engine_model: str, k: int = 6, manual_id: str | None = None) -> list[dict]:
    manuals = manuals_for_engine(engine_model)
    ids = tuple(sorted(m.id for m in manuals if manual_id in (None, m.id)))
    if not ids:
        return []
    idx = _index_for(ids)
    q = tokenize(query)
    if not q or idx.bm25 is None:
        return []
    scores = idx.bm25.get_scores(q)
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    results, seen_pages = [], set()
    for i in order:
        if scores[i] <= 0 or len(results) >= k:
            break
        c = idx.chunks[i]
        key = (c.manual_id, c.pdf_page)
        if key in seen_pages:
            continue
        seen_pages.add(key)
        m = BY_ID[c.manual_id]
        results.append({
            "manual": m.designation,
            "manual_id": m.id,
            "doc_kind": m.kind,
            "pdf_page": c.pdf_page,
            "score": round(float(scores[i]), 2),
            "ocr": c.source == "ocr",
            "text": c.text,
        })
    return results


def get_page(manual_id: str, pdf_page: int) -> str:
    return "\n".join(c.text for c in _all_chunks() if c.manual_id == manual_id and c.pdf_page == pdf_page)


def manual_mentions(engine_model: str, pattern: str) -> int:
    """Сколько страниц в руководствах на двигатель содержит шаблон (для проверки применимости типа неисправности)."""
    rx = re.compile(pattern, re.IGNORECASE)
    ids = {m.id for m in manuals_for_engine(engine_model)}
    return len({(c.manual_id, c.pdf_page) for c in _all_chunks() if c.manual_id in ids and rx.search(c.text)})
