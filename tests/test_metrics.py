from memor.eval.metrics import recall_at_k, ndcg_at_k

def test_recall_and_ndcg():
    retrieved = ["a","b","c","d"]
    relevant = {"b","d","z"}
    assert recall_at_k(retrieved, relevant, k=4) == 2/3   # found 2 of 3 relevant
    assert recall_at_k(retrieved, relevant, k=1) == 0.0   # 'a' not relevant
    # ndcg: relevant at ranks 2 and 4 -> between 0 and 1, and rank-2-first beats rank-4-first
    high = ndcg_at_k(["b","a","d","c"], relevant, k=4)
    low = ndcg_at_k(["a","c","b","d"], relevant, k=4)
    assert 0 < low < high <= 1.0
