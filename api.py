import os
from fastapi import FastAPI, UploadFile, File, Header, HTTPException
from pydantic import BaseModel
from src.ingestion.run import update
from src.agent.chat import answer
from src.clinical.cases import ingest_case, similar_cases
from src.multimodal.radiograph import analyze_image
import tempfile, os

app = FastAPI(title="OdontoAI")

@app.get("/health")
def health():
    return {"status": "ok"}

API_KEY = os.getenv("API_KEY", "")

def check_key(x_api_key: str = Header(default="")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")


class Question(BaseModel):
    question: str
    k: int = 5


@app.post("/chat", dependencies=[])
def chat(q: Question, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    return {"answer": answer(q.question, k=q.k)}


@app.post("/update")
def run_update(days: int = 1, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    return update(days=days)


@app.post("/cases")
def add_case(case: dict):
    return {"chunks": ingest_case(case)}


@app.get("/cases/similar")
def cases_similar(q: str, k: int = 3):
    return {"results": similar_cases(q, k=k)}


@app.post("/radiograph")
async def radiograph(file: UploadFile = File(...), question: str = "Descreva achados radiográficos relevantes."):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        tmp.write(await file.read())
        path = tmp.name
    try:
        return {"analysis": analyze_image(path, question)}
    finally:
        os.remove(path)
