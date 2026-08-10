from __future__ import annotations
import difflib
import math
import time
from memor.types import Scope, Hit, RetrievalTrace
from memor.interfaces import Embedder, MemoryStore

EDGE_TYPES = ["fixes", "supersedes", "part_of", "derived_from"]

KIND_WEIGHTS = {
    "memory": 1.3,
    "session_chunk": 1.0,
    "note": 1.1,
}

# Half-life in days: memories older than this get half the recency boost
RECENCY_HALF_LIFE_DAYS = 14

# Reciprocal Rank Fusion constant. Larger = flatter (rank position matters less).
#: Raw-cosine floor for the dense channel. Kept in step with
#: memor.recall.DEFAULT_MIN_SIMILARITY: a Retriever built directly, as the eval
#: harness and CLI do, must not silently use a stricter gate than recall().
#: Measured on a real store, 0.0 rejected genuine matches by hundredths --
#: these static embeddings score a good match near 0.05, so a zero floor sits
#: inside the noise band. See DEFAULT_MIN_SIMILARITY for the sweep.
MIN_SIMILARITY_FLOOR = -0.05

RRF_K = 60

#: Reserved share of k for distilled memories (`kind="memory"`).
#:
#: Measured on the real store: memories are 9.3% of active artifacts but reached
#: only 3.0% of injected hits, a 0.33x lift. The cause is not embedding quality
#: (cosine to real prompts is 0.275 for memories against 0.273 for chunks) nor
#: index coverage (100% for both). It is competition: a prompt drawn from a live
#: session is near-duplicate text to the transcript chunk that recorded it, and
#: a paraphrase cannot out-score a verbatim copy of the question. Raising the
#: kind boost from 1.3 to 3.0 moved the share 2.8% -> 4.1%, which is the most a
#: scoring thumb can do against a candidate-generation problem.
#:
#: So memories get a lane instead of a thumb: they compete against each other
#: for a small guaranteed share, and only fill it if they clear the same
#: relevance gate as everything else. An empty lane is given back to the general
#: pool, so this can raise the memory share but never pad k with weak results.
MEMORY_LANE_SHARE = 0.25

#: A lane candidate must score at least this fraction of the best general-pool
#: hit to be admitted. Without a relative floor the lane hits supply parity by
#: promoting near-irrelevant memories -- measured at cosine 0.035 against 0.042
#: for the chunks they displaced, which buys the headline share at the cost of
#: the answer. Relative rather than absolute because these static embeddings
#: put a good match near 0.05 on some queries and near 0.7 on others, so a fixed
#: floor would be simultaneously too strict and too loose.
#:
#: Swept 0.0 to 1.0 over 50 real prompts, scoring promoted candidates against
#: the ones they displaced by true cosine (not `components["sim"]`, which is
#: 0.0 for the 46% of hits that arrive via the lexical channel and cannot rank
#: anything). Every setting was a net gain, so the floor trades share against
#: margin rather than correctness: 0.0 gives 10.2% share at +0.017 cosine, 0.25
#: gives 8.2% at +0.029. 0.25 is chosen because it lands nearest supply parity
#: (9.3%) while promoting candidates that beat what they replace by the wider
#: margin -- the goal is memories that earn the slot, not the biggest number.
LANE_RELATIVE_FLOOR = 0.25

#: A retrieved chunk that largely repeats the query teaches the agent nothing:
#: it is the transcript of the question being asked. 35% of top hits on the real
#: store contained more than half the query verbatim. Candidates above this
#: containment ratio are demoted below genuine matches rather than deleted,
#: because a chunk that quotes the question can still carry the answer after it.
SELF_RECALL_CONTAINMENT = 0.6

#: Below this length a query is not evidence of self-recall. A short search term
#: like "auth refresh" is fully contained in any genuinely relevant document, so
#: containment there measures relevance, not echo. Caught by an existing test
#: that asserts recency breaks a tie between two equally-matching artifacts:
#: both scored 1.0 containment and were demoted together, silently inverting the
#: ranking the test was checking.
SELF_RECALL_MIN_QUERY_CHARS = 80

#: Fallback for a candidate with no quality row, and the ceiling every quality
#: value is held to. The blended score below is a convex combination — every
#: other term is normalized to [0, 1] — so a quality term that escapes that
#: range stops being a tie-breaker and becomes the ranking. The store clamps on
#: read; this repeats it at the point of use so a store implementation that
#: forgets cannot silently un-rank retrieval.
NEUTRAL_QUALITY = 0.5


#: How much relevance is traded for novelty when selecting the final set.
#: 1.0 is pure relevance (the old behaviour); 0.0 is pure diversity. 0.7 keeps
#: relevance firmly in charge and only intervenes when a candidate is close to
#: something already chosen.
MMR_LAMBDA = 0.7

#: Above this cosine to an already-selected memory, a candidate is telling the
#: caller something it has just been told.
MMR_NEAR_DUPLICATE = 0.92


def _cosine(a, b) -> float:
    dot = num = 0.0
    na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


def mmr_select(ranked, vectors: dict, k: int, *, lam: float = MMR_LAMBDA):
    """Re-order by Maximal Marginal Relevance: relevance minus redundancy.

    Plain top-k is redundancy-blind, and this store is 22.7% duplicates — one
    live recall spent three of five slots on the same text, so the caller paid
    for five memories and received three. MMR penalises a candidate by its
    similarity to what has already been chosen, which is the standard fix
    (Carbonell & Goldstein 1998) and costs a fraction of a millisecond at these
    pool sizes.

    Candidates without a vector are never dropped for lack of one — they simply
    cannot be checked for redundancy, so they compete on relevance alone.
    """
    if k <= 0 or len(ranked) <= 1:
        return list(ranked)[:k]

    remaining = list(ranked)
    best = max(h.score for h in remaining) or 1.0
    selected = []
    while remaining and len(selected) < k:
        best_idx, best_val = 0, None
        for idx, hit in enumerate(remaining):
            relevance = hit.score / best
            redundancy = 0.0
            vec = vectors.get(hit.artifact.id)
            if vec is not None and selected:
                for chosen in selected:
                    other = vectors.get(chosen.artifact.id)
                    if other is not None:
                        redundancy = max(redundancy, _cosine(vec, other))
            value = lam * relevance - (1.0 - lam) * redundancy
            # A near-identical candidate adds nothing, so it is pushed below
            # everything else rather than merely discounted. Suppressed at
            # lam=1.0 so that setting stays a true escape hatch to the previous
            # relevance-only behaviour — one knob, one meaning.
            if lam < 1.0 and redundancy >= MMR_NEAR_DUPLICATE:
                value -= 1.0
            if best_val is None or value > best_val:
                best_idx, best_val = idx, value
        selected.append(remaining.pop(best_idx))
    return selected



def _containment(query: str, text: str) -> float:
    """Fraction of the query that appears verbatim inside `text`.

    Uses the longest common contiguous run rather than token overlap: the target
    is a transcript that literally quotes the prompt, not a document that merely
    shares vocabulary with it. Comparison is bounded to the first 300/2000
    characters so cost stays flat on long chunks.
    """
    if not query or not text or len(query) < SELF_RECALL_MIN_QUERY_CHARS:
        return 0.0
    q = query[:300].lower()
    body = text[:2000].lower()
    match = difflib.SequenceMatcher(None, q, body).find_longest_match(
        0, len(q), 0, len(body))
    return match.size / len(q)


def select_with_memory_lane(ranked, k: int, *, share: float = MEMORY_LANE_SHARE):
    """Take the top k, reserving a share of the slots for distilled memories.

    The lane is a floor, not a quota: memories that already win on score fill it
    for free, and the reserved slots are released back to the general pool when
    no memory is available. So this can only change which candidates occupy the
    tail of k, never how many results are returned.
    """
    if k <= 0:
        return []
    reserved = int(k * share)
    if reserved <= 0:
        return list(ranked)[:k]

    memories = [h for h in ranked if h.artifact.kind == "memory"]
    if not memories:
        return list(ranked)[:k]

    general = list(ranked)[:k]
    already = sum(1 for h in general if h.artifact.kind == "memory")
    missing = max(0, reserved - already)
    if missing == 0:
        return general

    promote = [h for h in memories if h not in general][:missing]
    if not promote:
        return general

    # Drop the weakest non-memory hits to make room, preserving overall order.
    keep = [h for h in general if h.artifact.kind == "memory"]
    others = [h for h in general if h.artifact.kind != "memory"]
    others = others[:max(0, k - len(keep) - len(promote))]
    merged = keep + others + promote
    merged.sort(key=lambda h: h.score, reverse=True)
    return merged[:k]


def _bounded_quality(scores: dict, aid: str) -> float:
    value = scores.get(aid, NEUTRAL_QUALITY)
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return NEUTRAL_QUALITY


def rrf_fuse(ranked_lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion: combine several ranked id-lists into one score
    map. Each list contributes 1/(k + rank) per item (rank is 1-indexed).
    Items present in more lists, or ranked higher, score higher. The union of
    all lists is scored, so an item found by only one channel still surfaces."""
    scores: dict[str, float] = {}
    for ids in ranked_lists:
        for rank, _id in enumerate(ids, start=1):
            scores[_id] = scores.get(_id, 0.0) + 1.0 / (k + rank)
    return scores


class Retriever:
    def __init__(self, store: MemoryStore, embedder: Embedder, *,
                 k: int = 8, recency_weight: float = 0.25,
                 kind_weight: float = 0.15, quality_weight: float = 0.10,
                 min_similarity: float = MIN_SIMILARITY_FLOOR, edge_expand: bool = True,
                 use_keys: bool = False, memory_lane: float = MEMORY_LANE_SHARE,
                 suppress_self_recall: bool = True):
        self.store, self.embedder = store, embedder
        self.k, self.edge_expand = k, edge_expand
        self.min_similarity = min_similarity
        self.use_keys = use_keys
        self.w_sim = 1.0 - recency_weight - kind_weight - quality_weight
        self.w_rec = recency_weight
        self.w_kind = kind_weight
        self.w_qual = quality_weight
        self.memory_lane = memory_lane
        self.suppress_self_recall = suppress_self_recall

    def query(self, text: str, scope: Scope) -> RetrievalTrace:
        t0 = time.perf_counter()
        now = time.time()
        qv = self.embedder.embed([text])[0]
        if self.use_keys:
            return self._query_keys(text, qv, scope, t0, now)
        dense = self.store.search(qv, scope, self.k)

        # Absolute-similarity gate: drop anti-correlated candidates BEFORE
        # fusion/blending. Min-max normalization forces the top hit to a
        # normalized score of 1.0 regardless of its true cosine, so a
        # blended-score threshold cannot reject semantically-irrelevant results.
        # Gating on raw cosine here is the real relevance filter. (Static
        # embeddings put relevant content at >0 and noise at <0, so the default
        # floor is 0.0.) Set min_similarity below -1.0 to disable the gate.
        dense = [(a, sim) for a, sim in dense if sim >= self.min_similarity]

        # Lexical channel: BM25 over the exact text recovers rare identifiers /
        # error strings that the static dense embedding collapses, then RRF fuses
        # the two rankings. It only activates when the dense channel found the
        # query on-topic at all (gated dense is non-empty). Dense cleanly
        # separates on-topic (positive cosine) from off-topic (negative), so this
        # stops a generic multi-word query from OR-matching weak, off-topic terms
        # when nothing in the project is actually relevant.
        lexical = []
        if dense and hasattr(self.store, 'search_lexical'):
            lexical = self.store.search_lexical(text, scope, self.k)

        # Memory lane: a separate KNN restricted to distilled memories.
        #
        # Re-ranking the general pool is not enough, because the pool rarely
        # contains a memory to promote: measured on the real store, memories are
        # 4.2% of the dense top-8 and 4.1% of the top-32, so widening does not
        # help. A dedicated search surfaced candidates for 38 of 60 real queries
        # that the general pool never saw. This is only possible because the
        # kinds filter now selects the top k *of that kind* rather than
        # filtering an already-truncated slice.
        #
        # Gated by the same min_similarity floor as the general channel, so a
        # project with no relevant memory contributes nothing rather than
        # padding the results with its least-irrelevant one.
        if dense and self.memory_lane > 0:
            lane_scope = Scope(project=scope.project, kinds=["memory"],
                               since=scope.since, until=scope.until)
            try:
                lane = self.store.search(qv, lane_scope, self.k)
            except Exception:
                lane = []
            # Admit a lane candidate only if it is competitive with what the
            # general pool already found. Without this the lane reaches supply
            # parity by injecting near-zero-similarity memories: measured, the
            # promoted ones averaged cosine 0.035 against 0.042 for the chunks
            # they displaced, so the share improved while the results got worse.
            best_general = max((s for _, s in dense), default=0.0)
            admit = max(self.min_similarity, best_general * LANE_RELATIVE_FLOOR)
            seen = {x.id for x, _ in dense}
            for a, sim in lane:
                if sim >= admit and a.id not in seen:
                    dense.append((a, sim))

        arts_by_id: dict[str, object] = {}
        sim_by_id: dict[str, float] = {}
        for a, sim in dense:
            arts_by_id[a.id] = a
            sim_by_id[a.id] = sim
        for a, _ in lexical:
            arts_by_id.setdefault(a.id, a)

        candidates = len(arts_by_id)
        hits: dict[str, Hit] = {}

        fused = rrf_fuse([[a.id for a, _ in dense], [a.id for a, _ in lexical]])
        rel_vals = list(fused.values())
        rel_min = min(rel_vals) if rel_vals else 0.0
        rel_range = ((max(rel_vals) - rel_min) if rel_vals else 1.0) or 1.0

        if hasattr(self.store, 'get_quality_scores'):
            quality_scores = self.store.get_quality_scores(list(arts_by_id))
        else:
            quality_scores = {}

        for aid, a in arts_by_id.items():
            norm_rel = (fused.get(aid, 0.0) - rel_min) / rel_range

            age_days = (now - a.created_at) / 86400
            recency = math.exp(-0.693 * age_days / RECENCY_HALF_LIFE_DAYS)

            kind_boost = KIND_WEIGHTS.get(a.kind, 1.0) - 1.0

            quality = _bounded_quality(quality_scores, aid)

            score = (self.w_sim * norm_rel + self.w_rec * recency
                     + self.w_kind * kind_boost + self.w_qual * quality)
            hits[aid] = Hit(a, score, {
                "sim": sim_by_id.get(aid, 0.0), "rel": round(norm_rel, 3),
                "recency": round(recency, 3), "kind": a.kind,
                "quality": round(quality, 3), "edge": 0.0,
            })

        if self.edge_expand and arts_by_id:
            seed_ids = list(arts_by_id.keys())
            for nb in self.store.neighbors(seed_ids, EDGE_TYPES, hops=1):
                if nb.id not in hits:
                    hits[nb.id] = Hit(nb, 0.5 * max(h.score for h in hits.values()),
                                      {"sim": 0.0, "rel": 0.0,
                                       "recency": 0.0, "kind": nb.kind, "edge": 1.0})

        ranked = sorted(hits.values(), key=lambda h: h.score, reverse=True)
        # Diversify over a wider slice than k, so there is something to choose
        # between; taking k first would leave nothing to swap a duplicate for.
        pool = ranked[:max(self.k * 4, self.k)]
        pool = self._demote_self_recall(text, pool)
        pool = mmr_select(pool, self._vectors_for(pool), len(pool))
        ranked = select_with_memory_lane(pool, self.k, share=self.memory_lane)
        return RetrievalTrace(query=text, scope=scope, candidates=candidates,
                              hits=ranked, latency_ms=(time.perf_counter()-t0)*1000)

    def _demote_self_recall(self, text: str, hits):
        """Push candidates that merely quote the query below genuine matches.

        A prompt taken from a live session is near-duplicate text to the
        transcript chunk that recorded it, so the dense channel ranks that chunk
        first for reasons that carry no information: the agent already has the
        question. Demotion rather than deletion, because the chunk that quotes
        the prompt often continues into the answer.

        Distilled memories are exempt. They are paraphrases by construction, so
        high containment there means the memory is genuinely about this topic.
        """
        if not self.suppress_self_recall or not hits:
            return hits
        adjusted = []
        for h in hits:
            if h.artifact.kind != "memory":
                ratio = _containment(text, h.artifact.text)
                if ratio >= SELF_RECALL_CONTAINMENT:
                    components = dict(h.components)
                    components["self_recall"] = round(ratio, 3)
                    adjusted.append(Hit(h.artifact, h.score - 1.0, components))
                    continue
            adjusted.append(h)
        adjusted.sort(key=lambda x: x.score, reverse=True)
        return adjusted

    def _vectors_for(self, hits) -> dict:
        """Embeddings for candidates, for the redundancy term. Best effort.

        A store that cannot return vectors degrades to relevance-only ranking,
        which is the behaviour that preceded this — never an error.
        """
        ids = [h.artifact.id for h in hits]
        if not ids or not hasattr(self.store, "vectors_for"):
            return {}
        try:
            return self.store.vectors_for(ids)
        except Exception:
            return {}

    def _query_keys(self, text, qv, scope, t0, now):
        key_hits = self.store.search_keys(qv, scope, self.k * 4)  # [(mid, sim)]
        if not key_hits:
            return RetrievalTrace(query=text, scope=scope, candidates=0,
                                  hits=[], latency_ms=(time.perf_counter()-t0)*1000)
        sim_by_id = dict(key_hits)
        # Apply min_similarity gate before any scoring
        sim_by_id = {mid: s for mid, s in sim_by_id.items() if s >= self.min_similarity}
        if not sim_by_id:
            return RetrievalTrace(query=text, scope=scope, candidates=0,
                                  hits=[], latency_ms=(time.perf_counter()-t0)*1000)

        # Lexical channel: BM25 over key_text via fts_keys. Only activates when
        # dense key search is non-empty (mirrors the main dense path's gate).
        lex_hits: list[tuple[str, float]] = []
        if hasattr(self.store, 'search_keys_lexical'):
            lex_hits = self.store.search_keys_lexical(text, scope, self.k * 4)

        # Build ranked id lists for RRF fusion
        vec_ids = sorted(sim_by_id, key=lambda m: sim_by_id[m], reverse=True)
        lex_ids = [mid for mid, _ in lex_hits]

        fused = rrf_fuse([vec_ids, lex_ids])

        # Candidate id set: union of fused keys (both channels)
        all_ids = list(fused)
        qmarks = ",".join("?" * len(all_ids))
        rows = self.store.db.execute(
            f"SELECT * FROM artifacts WHERE id IN ({qmarks}) AND active=1", all_ids).fetchall()
        arts = {r["id"]: self.store._row_to_artifact(r) for r in rows}
        if not arts:
            return RetrievalTrace(query=text, scope=scope, candidates=0,
                                  hits=[], latency_ms=(time.perf_counter()-t0)*1000)

        # Normalize fused scores over active ids only
        quality = self.store.get_quality_scores(list(arts)) if hasattr(self.store, 'get_quality_scores') else {}
        active_fused = [fused[mid] for mid in arts]
        rel_min = min(active_fused)
        rel_range = (max(active_fused) - rel_min) or 1.0

        hits = []
        for mid, a in arts.items():
            norm_rel = (fused.get(mid, 0.0) - rel_min) / rel_range
            age_days = (now - a.created_at) / 86400
            recency = math.exp(-0.693 * age_days / RECENCY_HALF_LIFE_DAYS)
            kind_boost = KIND_WEIGHTS.get(a.kind, 1.0) - 1.0
            q = _bounded_quality(quality, mid)
            score = (self.w_sim * norm_rel + self.w_rec * recency
                     + self.w_kind * kind_boost + self.w_qual * q)
            # "sim" debug field = raw cosine from vec channel (0.0 for lexical-only hits)
            hits.append(Hit(a, score, {"sim": round(sim_by_id.get(mid, 0.0), 3),
                                       "rel": round(norm_rel, 3),
                                       "recency": round(recency, 3), "kind": a.kind,
                                       "quality": round(q, 3), "edge": 0.0}))
        hits.sort(key=lambda h: h.score, reverse=True)
        return RetrievalTrace(query=text, scope=scope, candidates=len(arts),
                              hits=hits[:self.k], latency_ms=(time.perf_counter()-t0)*1000)
