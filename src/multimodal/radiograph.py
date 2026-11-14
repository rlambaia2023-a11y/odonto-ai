"""Análise multimodal de radiografias (v2/v3)."""
# Placeholder: integrar com modelo de visão (GPT-4o / Claude 3.5 Sonnet)
def analyze_image(image_path: str, question: str = "Descreva achados radiográficos relevantes.") -> str:
    from ..config import LLM_PROVIDER, OPENAI_API_KEY
    import base64
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    if LLM_PROVIDER == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        r = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}],
        )
        return r.choices[0].message.content
    return "Provider não suportado para visão."
