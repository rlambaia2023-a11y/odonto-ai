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
Corrige provas mistas (objetivas + discursivas), feitas em papel ou no computador, em turmas grandes.
Objetivas: na hora, pelo gabarito, sem IA. Discursivas: IA seguindo a rubrica do professor, em paralelo.
Marcação ambígua, trecho ilegível ou baixa confiança da IA saem marcados para revisão. A nota final é do professor.

1. Gabarito/rubrica: modelo em `examples/gabarito_exemplo.json`.
2. Provas em papel: digitalize (um PDF/foto por aluno numa pasta, ou um PDF único da turma) e transcreva:
```bash
python -m src.grading.cli transcrever gabarito.json PASTA_DAS_PROVAS -o respostas_papel.csv
python -m src.grading.cli transcrever gabarito.json turma.pdf --paginas-por-aluno 2 -o respostas_papel.csv
```
   Confira as linhas com a coluna `conferir` preenchida e apague esse texto depois de conferir.
3. Provas no computador: CSV com coluna `aluno` + `Q1`, `Q2`... (Google Forms, Moodle). Modelo em `examples/respostas_exemplo.csv`.
4. Corrija tudo junto:
```bash
python -m src.grading.cli corrigir gabarito.json respostas_papel.csv respostas_online.csv -o notas.csv
```
Velocidade: `GRADING_MAX_WORKERS` (padrão 8) define quantas respostas a IA corrige ao mesmo tempo;
suba até o limite de taxa da sua conta. Também disponível na API (`/grading/*`) e na tela **Corrigir provas** do Streamlit.
