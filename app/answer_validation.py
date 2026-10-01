"""Source selectors build answers on the server; legacy alignment is diagnostic."""
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


def sentence_catalog(hits: list[dict]) -> dict[str, dict]:
    catalog = {}
    for hit in hits[:4]:
        for index, text in enumerate(sentences(hit["content"])):
            # Heading-only lines are context, not a complete answer step.
            if not text.endswith(("。", "！", "？")):
                continue
            catalog[f"{hit['id']}:{index}"] = {
                "text": text, "chunk_id": hit["id"], "version": hit["version"],
                "chunk_key": hit.get("chunk_key", ""), "title": hit["title"]}
    return catalog


def render_selection(selected, catalog: dict) -> tuple[str | None, list[int], dict]:
    valid_shape = (isinstance(selected, list) and 1 <= len(selected) <= 8
                   and all(type(value) is str for value in selected))
    if not valid_shape or any(value not in catalog for value in selected):
        return None, [], {"method": "source_selection_v2", "passed": False,
                          "reason": "invalid_selector", "sentences": []}
    rows = [{"selector": value, **catalog[value]} for value in dict.fromkeys(selected)]
    return "\n".join(row["text"] for row in rows), list(dict.fromkeys(row["chunk_id"] for row in rows)), {
        "method": "source_selection_v2", "passed": True, "reason": "rendered_from_source", "sentences": rows}
