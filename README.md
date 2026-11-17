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

## Corretor de provas (`src/grading/`)
Corrige as provas de uma turma: objetivas na hora pelo gabarito (sem IA) e discursivas pela IA
seguindo a rubrica do professor, em paralelo. Respostas ambíguas, ilegíveis ou com baixa confiança
da IA saem marcadas `revisar = SIM` na planilha. A nota final é sempre do professor.

1. Preencha o gabarito/rubrica: modelo em `examples/gabarito_exemplo.json`.
2. Exporte as respostas da turma em CSV (coluna `aluno` + `Q1`, `Q2`...): modelo em `examples/respostas_exemplo.csv`.
3. Corrija:
```bash
python -m src.grading.cli examples/gabarito_exemplo.json examples/respostas_exemplo.csv -o notas.csv
```
Também disponível na API (`POST /grading/corrigir`, `/grading/corrigir-planilha`, `/grading/transcrever`
para foto de prova em papel) e na tela **Corrigir provas** do Streamlit.
