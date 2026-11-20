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
