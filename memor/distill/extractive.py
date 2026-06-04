"""Extractive distillation — LLM-free, local-only.

Picks the highest-signal chunks from a session using:
1. TF-IDF scoring (rare/specific terms score higher)
2. Embedding clustering (one representative per topic cluster)
3. Heuristic rules (drop filler, prefer long structured responses + user questions)
"""
from __future__ import annotations
import hashlib, math, re
from collections import Counter
from memor.types import Artifact

_DECISION_RE = re.compile(
    r"(we decided|the approach is|instead of|switched to|chose .+ over|"
    r"trade-?off|architecture:|design decision)", re.I)
_BUGFIX_RE = re.compile(
    r"(the fix is|root cause|the bug was|the issue was|caused by|"
    r"the problem is|this fails because|the error occurs)", re.I)
_LESSON_RE = re.compile(
    r"(always use|never use|never do|important:|note:|pattern:|"
    r"best practice|lesson learned|rule of thumb|should always|should never)", re.I)
_SNIPPET_RE = re.compile(r"```")


def classify_chunk(text: str) -> str:
    """Classify a chunk into a memory type based on content patterns."""
    if _BUGFIX_RE.search(text):
        return "bugfix"
    if _DECISION_RE.search(text):
        return "decision"
    if _LESSON_RE.search(text):
        return "lesson"
    if _SNIPPET_RE.search(text) and len(text) > 200:
        return "snippet"
    return "extract"

MIN_CHUNK_TOKENS = 15
MAX_EXTRACTS = 12

_FILLER = re.compile(
    r"^(Let me |Now let me |Now I|Good[,.]|Great[,.]|Perfect[!,.]|Done[!,.]"
    r"|Sure[,.]|OK[,.]|Alright|Looking at |Checking |I'll )", re.I)

_SIGNAL_PATTERNS = re.compile(
    r"(we decided|the fix is|the solution|use .+ instead of|should always|"
    r"never use|important:|note:|bug:|pattern:|architecture:)", re.I)


def _tfidf_scores(chunks: list[Artifact]) -> list[float]:
    """Score each chunk by TF-IDF — chunks with rare, specific terms rank higher."""
    tokenize = lambda t: re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", t.lower())
    doc_freq: Counter[str] = Counter()
    chunk_tokens: list[list[str]] = []
    for c in chunks:
        toks = tokenize(c.text)
        chunk_tokens.append(toks)
        doc_freq.update(set(toks))
    n = len(chunks) or 1
    scores = []
    for toks in chunk_tokens:
        if not toks:
            scores.append(0.0)
            continue
        tf = Counter(toks)
        score = sum(
            (tf[t] / len(toks)) * math.log((n + 1) / (doc_freq[t] + 1))
            for t in tf
        )
        scores.append(score)
    return scores


def _heuristic_score(chunk: Artifact) -> float:
    """Bonus/penalty based on content heuristics."""
    score = 0.0
    if chunk.token_count < MIN_CHUNK_TOKENS:
        return -1.0
    if _FILLER.match(chunk.text):
        score -= 0.5
    if _SIGNAL_PATTERNS.search(chunk.text):
        score += 1.0
    role = chunk.meta.get("role", "")
    if role == "user" and chunk.token_count > 20:
        score += 0.3
    if role == "assistant" and chunk.token_count > 100:
        score += 0.5
    if "```" in chunk.text:
        score += 0.2
    return score


def _cluster_select(chunks: list[Artifact], embedder, max_clusters: int) -> list[int]:
    """Cluster chunks by embedding similarity, return index of chunk nearest each centroid."""
    if len(chunks) <= max_clusters:
        return list(range(len(chunks)))
    vecs = embedder.embed([c.text for c in chunks])
    n = len(vecs)
    dim = len(vecs[0])
    # Simple k-means (few iterations, good enough for <300 chunks)
    k = min(max_clusters, n)
    centroids = [list(vecs[i]) for i in range(0, n, max(1, n // k))][:k]
    assignments = [0] * n
    for _ in range(8):
        for i in range(n):
            best_d, best_j = float("inf"), 0
            for j, c in enumerate(centroids):
                d = sum((a - b) ** 2 for a, b in zip(vecs[i], c))
                if d < best_d:
                    best_d, best_j = d, j
            assignments[i] = best_j
        new_centroids = [[0.0] * dim for _ in range(k)]
        counts = [0] * k
        for i in range(n):
            j = assignments[i]
            counts[j] += 1
            for d_idx in range(dim):
                new_centroids[j][d_idx] += vecs[i][d_idx]
        for j in range(k):
            if counts[j] > 0:
                for d_idx in range(dim):
                    new_centroids[j][d_idx] /= counts[j]
        centroids = new_centroids
    # Pick chunk nearest each centroid
    selected = []
    for j in range(k):
        members = [i for i in range(n) if assignments[i] == j]
        if not members:
            continue
        best_i = min(members, key=lambda i: sum(
            (vecs[i][d] - centroids[j][d]) ** 2 for d in range(dim)))
        selected.append(best_i)
    return selected


def extract_key_chunks(
    chunks: list[Artifact], embedder, *, max_extracts: int = MAX_EXTRACTS
) -> list[Artifact]:
    """Select the highest-signal chunks from a session. Pure local, no LLM."""
    if not chunks:
        return []
    # Score each chunk: TF-IDF + heuristic
    tfidf = _tfidf_scores(chunks)
    max_tf = max(tfidf) or 1.0
    combined = []
    for i, c in enumerate(chunks):
        h = _heuristic_score(c)
        if h <= -1.0:
            combined.append(-999.0)
            continue
        combined.append((tfidf[i] / max_tf) + h)
    # Pre-filter: drop anything scored below 0
    viable_idx = [i for i, s in enumerate(combined) if s > 0]
    if not viable_idx:
        viable_idx = list(range(len(chunks)))
    viable = [chunks[i] for i in viable_idx]
    # Cluster the viable chunks and pick representatives
    cluster_idx = _cluster_select(viable, embedder, max_extracts)
    selected = [viable[i] for i in cluster_idx]
    # Sort by original order (temporal coherence)
    selected.sort(key=lambda c: c.meta.get("ord", 0))
    return selected[:max_extracts]
