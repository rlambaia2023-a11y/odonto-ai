# OdontoAI — Assistente de IA para Odontologia (atualização diária)

IA especializada em odontologia, com base de conhecimento que se atualiza todos os dias
via pipeline de ingestão de artigos científicos (PubMed), guidelines (ADA, OMS, CFO, Anvisa)
e fontes oficiais. Arquitetura RAG + agente conversacional.

## Estrutura
- `src/ingestion/` — coletores e normalização de fontes
- `src/rag/` — chunking, embeddings, vector store, retriever
- `src/agent/` — agente conversacional com citações
- `scheduler/` — jobs diários de atualização
- `data/` — armazenamento local (vector db, cache)

## Roadmap
- v0: MVP RAG + chat CLI
- v1: atualização diária automatizada + citações
- v2: casos clínicos anonimizados + planejamento de tratamento
- v3: integração com prontuário / multimodal (radiografias)

## Setup
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```
