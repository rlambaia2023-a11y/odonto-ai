"""Coletor de guidelines/notícias via RSS."""
import feedparser
from ..config import GUIDELINE_FEEDS


def fetch_feeds() -> list[dict]:
    docs = []
    for url in GUIDELINE_FEEDS:
        feed = feedparser.parse(url)
        for e in feed.entries[:30]:
            docs.append({
                "source": "guideline",
                "id": getattr(e, "id", e.get("link", "")),
                "title": e.get("title", ""),
                "text": e.get("summary", "") or e.get("description", ""),
                "url": e.get("link", ""),
            })
    return docs
