import os
from dotenv import load_dotenv

# O .env do projeto prevalece sobre variáveis herdadas de outros programas (no servidor não há .env).
load_dotenv(override=True)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
CHROMA_PATH = os.getenv("CHROMA_PATH", "./data/chroma")
PUBMED_QUERY = os.getenv("PUBMED_QUERY", "(dentistry OR odontology) AND clinical")
PUBMED_MAX_RESULTS = int(os.getenv("PUBMED_MAX_RESULTS", "50"))
GUIDELINE_FEEDS = [f for f in os.getenv("GUIDELINE_FEEDS", "").split(",") if f]
UPDATE_HOUR = int(os.getenv("UPDATE_HOUR", "3"))
COLLECTION_NAME = "odonto_knowledge"
SYSTEM_PROMPT = (
    "Você é OdontoAI, assistente especializado em odontologia. "
    "Responda com base no contexto fornecido, cite as fontes quando possível, "
    "e indique quando não houver evidência suficiente. Não substitua avaliação clínica presencial."
)
