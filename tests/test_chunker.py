from src.rag.chunker import chunk_docs

def test_chunk_docs_basic():
    docs = [{"source": "pubmed", "id": "1", "title": "T", "text": "a " * 200, "url": ""}]
    chunks = chunk_docs(docs, chunk_size=100, overlap=10)
    assert len(chunks) > 1
    assert all(c["metadata"]["source"] == "pubmed" for c in chunks)

def test_dedup_run():
    from src.ingestion.run import _dedup
    docs = [{"title": "x", "text": "abc"}, {"title": "x", "text": "abc"}]
    assert len(_dedup(docs)) == 1
