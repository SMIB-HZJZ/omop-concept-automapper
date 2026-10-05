from pydantic import BaseModel, ConfigDict, Field

from app.workflows.context import NodeContext
from app.workflows.registry import router
from app.workflows.state import AutomapState


class GateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Overrides the threshold from the request when set
    threshold: int | None = Field(default=None, ge=1, le=10)


@router(
    "confidence_gate",
    routes={"accept", "reject"},
    params=GateParams,
    description="Accept the selection if its confidence reaches the threshold",
)
def confidence_gate(state: AutomapState, ctx: NodeContext, params: GateParams):
    threshold = params.threshold or state.get("threshold") or 8
    confidence = state.get("confidence")
    if state.get("selected") and confidence is not None and confidence >= threshold:
        return "accept", {
            "outcome": "accepted",
            "_summary": f"confidence {confidence} ≥ {threshold}",
        }
    outcome = state.get("outcome")
    if outcome not in ("no_candidates", "no_match", "error"):
        outcome = "rejected"
    return "reject", {"outcome": outcome, "_summary": f"confidence {confidence} < {threshold}"}
