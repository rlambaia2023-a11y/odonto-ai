"""Coletor de artigos do PubMed (odontologia)."""
from Bio import Entrez
from ..config import PUBMED_QUERY, PUBMED_MAX_RESULTS

Entrez.email = "odontoai@example.com"


from ..utils import retry

@retry(times=3, delay=2.0)
def fetch_recent(days: int = 1, max_results: int = None) -> list[dict]:
    max_results = max_results or PUBMED_MAX_RESULTS
    handle = Entrez.esearch(db="pubmed", term=PUBMED_QUERY, retmax=max_results,
                            reldate=days, datetype="pdat")
    ids = Entrez.read(handle)["IdList"]
    handle.close()
    if not ids:
        return []
    handle = Entrez.efetch(db="pubmed", id=",".join(ids), rettype="abstract", retmode="xml")
    records = Entrez.read(handle)
    handle.close()
    docs = []
    for art in records.get("PubmedArticle", []):
        med = art["MedlineCitation"]["Article"]
        abstract = med.get("Abstract", {}).get("AbstractText", [])
        text = " ".join(str(a) for a in abstract) if abstract else ""
        docs.append({
            "source": "pubmed",
            "id": str(art["MedlineCitation"]["PMID"]),
            "title": med.get("ArticleTitle", ""),
            "text": text,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{art['MedlineCitation']['PMID']}/",
        })
    return docs
