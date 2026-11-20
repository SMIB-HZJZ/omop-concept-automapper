"""Short, user-facing names of pipeline steps, for live progress (AI suggest, auto-mapping)."""

STEP_LABELS = {
    "match_previous": "Checking earlier mappings",
    "match_code": "Looking up the source code",
    "match_name": "Exact name match",
    "match_drug": "Matching the drug",
    "translate": "Translating",
    "retrieve": "Searching",
    "llm_rerank": "AI pick",
    "rewrite_search": "Rewriting the search",
    "agentic_refine": "Agent retry",
    "decide_with_evidence": "Final decision on all evidence",
}
# Steps that take no noticeable time are not shown
QUIET_STEPS = {"extract_atc7", "confidence_gate"}


def step_label(node_id: str | None, uses: str, detail: str | None = None) -> str | None:
    if detail:
        return detail
    if uses in QUIET_STEPS:
        return None
    label = STEP_LABELS.get(uses, uses.replace("_", " ").capitalize())
    if uses == "llm_rerank" and node_id and node_id != "rerank":
        label = "AI pick on the new candidates"
    return label


# Methods of picks made for unsure terms (part 2 of the pipelines): not auto-committed
PART2_METHODS = frozenset({"auto_rewritten", "auto_agentic", "auto_final"})
_SHORT = {
    "AI pick": "AI pick",
    "second AI pick, after rewritten searches": "AI pick after rewrite",
    "agent retry": "agent retry",
    "final decision": "final decision",
}


def decision_path(state: dict) -> str | None:
    """How the result came about, e.g. "AI pick 6 → agent retry 7 → final decision 8". None for
    deterministic matches (earlier mapping, source code, exact name, drug match)."""
    parts = []
    for a in state.get("attempts") or []:
        label = _SHORT.get(a.get("step", ""), a.get("step", "step"))
        if a.get("route") == "escalated":
            label += " (stronger model)"
        found = str(a["confidence"]) if a.get("concept") and a.get("confidence") else "no match"
        parts.append(f"{label} {found}")
    return " → ".join(parts) or None
