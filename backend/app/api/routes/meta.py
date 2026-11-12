from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.api.deps import CurrentUser, SessionDep
from app.core.redis import get_redis
from app.llm.gateway import gateway_healthy
from app.schemas.api import Meta
from app.services.atc7 import ATC7_PATTERN
from app.services.concepts import list_domains
from app.services.settings_store import DIMENSION_OPTIONS
from app.services.users import ROLES

router = APIRouter(tags=["meta"])

# The domains offered by the Streamlit app; domains found in the vocabulary are added to these
DEFAULT_DOMAINS = ["Condition", "Device", "Drug", "Measurement", "Observation", "Procedure"]


@router.get("/health")
def health(session: SessionDep) -> dict:
    checks: dict[str, bool] = {}
    try:
        session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        checks["database"] = False
    try:
        checks["redis"] = bool(get_redis().ping())
    except Exception:
        checks["redis"] = False
    checks["llm_gateway"] = gateway_healthy()
    return {
        "status": "ok" if checks["database"] and checks["redis"] else "degraded",
        "checks": checks,
    }


@router.get("/meta", response_model=Meta)
def meta(_: CurrentUser, session: SessionDep):
    domains = sorted(set(DEFAULT_DOMAINS) | set(list_domains(session)))
    return Meta(
        domains=domains,
        atc7_pattern=ATC7_PATTERN,
        dimension_options=DIMENSION_OPTIONS,
        roles=list(ROLES),
        version=__version__,
    )
