"""Retrieval signals only: no gold labels or document topic IDs are features."""
FEATURE_NAMES = ["lexical_coverage", "top_similarity", "vector_margin", "bm25_margin",
                 "top_agreement", "overlap_at_3", "has_bm25", "has_vector"]


def retrieval_features(hits, terms, lexical, semantic):
    lexical_ids = [row["id"] for row in lexical[:3]]
    vector_ids = [row["id"] for row in semantic[:3]]
    distances = [float(row.get("raw_distance", row.get("distance", 0))) for row in lexical[:2]]
    bm25_margin = ((distances[1] - distances[0]) / max(abs(distances[0]), 1e-8)
                  if len(distances) > 1 else 1.0 if distances else 0.0)
    similarities = [1 - float(row.get("raw_distance", row.get("distance", 1))) for row in semantic[:2]]
    vector_margin = similarities[0] - similarities[1] if len(similarities) > 1 else 0.0
    top = hits[0] if hits else {}
    values = [top.get("matched_terms", 0) / max(len(set(terms)), 1), top.get("similarity") or 0,
              vector_margin, bm25_margin,
              float(bool(lexical_ids and vector_ids and lexical_ids[0] == vector_ids[0])),
              len(set(lexical_ids) & set(vector_ids)) / 3,
              float(bool(lexical_ids)), float(bool(vector_ids))]
    return {name: round(max(0.0, min(1.0, value)), 6) for name, value in zip(FEATURE_NAMES, values)}
