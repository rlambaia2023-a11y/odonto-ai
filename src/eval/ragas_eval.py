"""Avaliação de qualidade RAG (v3)."""
# Requer: pip install ragas datasets
def evaluate(dataset_path: str) -> dict:
    """dataset: JSONL com {question, answer, contexts, ground_truth}"""
    try:
        from ragas import evaluate as ragas_evaluate
        from ragas.metrics import faithfulness, answer_relevancy, context_precision
        from datasets import Dataset
        import json
        rows = [json.loads(l) for l in open(dataset_path, encoding="utf-8")]
        ds = Dataset.from_list(rows)
        result = ragas_evaluate(ds, metrics=[faithfulness, answer_relevancy, context_precision])
        return result.to_pandas().to_dict()
    except Exception as e:
        return {"error": str(e)}
