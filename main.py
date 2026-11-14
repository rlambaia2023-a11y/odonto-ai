"""CLI simples para testar a OdontoAI."""
import sys
from src.ingestion.run import update
from src.agent.chat import answer

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        print(update(days=int(sys.argv[2]) if len(sys.argv) > 2 else 1))
    else:
        q = " ".join(sys.argv[1:]) or input("Pergunta: ")
        print(answer(q))
