from app.services.atc7 import extract_atc7_codes_from_source
from app.workflows.context import NodeContext
from app.workflows.registry import node
from app.workflows.state import AutomapState


@node(
    "extract_atc7", description="Drug mode: take the ATC7 code from the start of the source value"
)
async def extract_atc7(state: AutomapState, ctx: NodeContext, params) -> dict:
    if not state.get("drug_specific"):
        return {"_summary": "skipped (not drug-specific)"}
    if state.get("atc7_codes"):
        return {"_summary": f"using provided ATC7 {', '.join(state['atc7_codes'])}"}
    codes = extract_atc7_codes_from_source(state.get("source_value"))
    return {
        "atc7_codes": codes,
        "_summary": f"found ATC7 {codes[0]}" if codes else "no ATC7 code in source value",
    }
