"""Pipeline diário de atualização."""
import hashlib, logging
from .pubmed import fetch_recent
from .guidelines import fetch_feeds
from ..rag.chunker import chunk_docs
from ..rag.vectorstore import upsert_chunks

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("odontoai.ingestion")


def _dedup(docs):
    seen = set()
    out = []
    for d in docs:
        key = hashlib.md5((d["title"] + d["text"][:200]).encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def update(days: int = 1) -> dict:
    docs = _dedup(fetch_recent(days=days) + fetch_feeds())
    chunks = chunk_docs(docs)
    n = upsert_chunks(chunks)
    log.info("Ingested %d docs -> %d chunks", len(docs), n)
    return {"docs": len(docs), "chunks": n}
