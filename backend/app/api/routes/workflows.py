from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.schemas.api import WorkflowCatalog
from app.workflows.service import catalog

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("", response_model=WorkflowCatalog)
def list_workflows(_: CurrentUser):
    return catalog()
