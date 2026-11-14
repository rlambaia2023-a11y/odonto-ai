import os
from ..config import LLM_PROVIDER, OPENAI_API_KEY, ANTHROPIC_API_KEY, SYSTEM_PROMPT
from ..rag.vectorstore import query


from ..utils import require_env

def answer(question: str, k: int = 5) -> str:
    require_env()
    ctx = query(question, k=k)
    context_text = "\n\n".join(f"[{c['metadata']['source']}] {c['text']}" for c in ctx)
    user_msg = f"Contexto:\n{context_text}\n\nPergunta: {question}"
    if LLM_PROVIDER == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": user_msg}],
        )
        reply = r.choices[0].message.content
    else:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        r = client.messages.create(
            model="claude-3-5-sonnet-latest", max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        reply = r.content[0].text
    # anexa fontes
    sources = "\n".join(f"- {c['metadata'].get('title','')} ({c['metadata'].get('url','')})" for c in ctx)
    return reply + "\n\nFontes:\n" + sources
