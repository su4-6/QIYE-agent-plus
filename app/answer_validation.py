"""Conservative extractive sentence alignment; this is not NLI or semantic proof."""
import re


def sentences(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", part).strip()
            for part in re.findall(r"[^。！？\n]+[。！？]?", text) if part.strip()]


def align_answer(answer: str, cited: list[int], hits: list[dict]) -> dict:
    sources = [hit for hit in hits if hit["id"] in cited]
    allowed = [(hit, set(sentences(hit["content"]))) for hit in sources]
    claims = sentences(answer)
    rows = []
    for index, claim in enumerate(claims):
        source = next((hit for hit, units in allowed if claim in units), None)
        rows.append({"sentence_index": index, "text": claim,
                     "chunk_id": source["id"] if source else None,
                     "version": source.get("version") if source else None,
                     "chunk_key": source.get("chunk_key", "") if source else ""})
    passed = bool(claims) and all(row["chunk_id"] is not None for row in rows)
    return {"method": "exact_source_sentence_v1", "passed": passed,
            "reason": "aligned" if passed else "empty_answer" if not claims else "unmatched_sentence",
            "sentences": rows}
