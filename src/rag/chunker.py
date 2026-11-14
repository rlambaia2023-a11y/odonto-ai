"""Chunking de documentos."""
from langchain.text_splitter import RecursiveCharacterTextSplitter


def chunk_docs(docs: list[dict], chunk_size: int = 800, overlap: int = 120) -> list[dict]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=overlap)
    out = []
    for d in docs:
        for c in splitter.split_text(d["text"] or d["title"]):
            out.append({"text": c, "metadata": {"source": d["source"], "id": d["id"],
                                                "title": d["title"], "url": d["url"]}})
    return out
