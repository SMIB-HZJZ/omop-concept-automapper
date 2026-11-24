"""Target domains and vocabularies of a mapping run, under one ``domain_mode``.

- restrict: only concepts in the chosen domains *and* vocabularies; deterministic matches outside
  them are dropped and every search is filtered.
- prefer: searches are filtered, a few candidates from outside are added, and the reranker is told
  the preference; deterministic matches are kept.
- all: neither is used.

An empty list means no filter of that kind: any domain, or any vocabulary.
"""

from app.workflows.state import AutomapState


def _mode(state: AutomapState) -> str:
    return state.get("domain_mode") or "restrict"


def targets(state: AutomapState) -> tuple[list[str], list[str]]:
    """The domains and vocabularies searches are filtered by (none with mode "all")."""
    if _mode(state) == "all":
        return [], []
    return list(state.get("domains") or []), list(state.get("vocabularies") or [])


def restricted(state: AutomapState) -> bool:
    return _mode(state) == "restrict"


def preferred(state: AutomapState) -> bool:
    domains, vocabularies = targets(state)
    return _mode(state) == "prefer" and bool(domains or vocabularies)


def fits(state: AutomapState, concept: dict) -> bool:
    """Whether a concept is in the chosen domains and vocabularies."""
    domains, vocabularies = targets(state)
    return (not domains or concept.get("domain_id") in domains) and (
        not vocabularies or concept.get("vocabulary_id") in vocabularies
    )


def in_scope(state: AutomapState, concepts: list[dict]) -> list[dict]:
    """With mode "restrict", the concepts in the chosen domains and vocabularies; else all."""
    if not restricted(state):
        return concepts
    return [c for c in concepts if fits(state, c)]


def describe(state: AutomapState) -> str:
    """'domains Condition, vocabularies SNOMED' for summaries and notes."""
    domains, vocabularies = targets(state)
    parts = []
    if domains:
        parts.append(f"domains {', '.join(domains)}")
    if vocabularies:
        parts.append(f"vocabularies {', '.join(vocabularies)}")
    return " and ".join(parts)
