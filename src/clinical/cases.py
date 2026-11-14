"""Casos clínicos anonimizados (v2)."""
from ..rag.vectorstore import upsert_chunks, query
from ..rag.chunker import chunk_docs


def ingest_case(case: dict) -> int:
    """case: {id, title, anamnese, exame, diagnostico, plano, outcome}"""
    text = "\n".join(f"{k}: {v}" for k, v in case.items() if k not in ("id",))
    chunks = chunk_docs([{"source": "case", "id": case["id"], "title": case.get("title", ""), "text": text, "url": ""}])
    return upsert_chunks(chunks)


def similar_cases(query_text: str, k: int = 3):
    return query(query_text, k=k)
