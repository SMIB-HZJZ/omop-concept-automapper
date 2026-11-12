"""The example plugin adds a step and a pipeline without changing any core file."""

import hashlib
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from app.llm.structured import RerankResult
from app.workflows import builder, plugins
from app.workflows.service import catalog, initial_state, run_pipeline
from tests.workflows.helpers import FakeSearcher, make_context

REPO = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = REPO / "examples" / "workflow_plugins" / "translate_query"


class FirstCandidate:
    async def rerank(self, term, candidates, drug_specific):
        return RerankResult(0, 9)


@pytest.fixture
def with_plugin(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        settings, "workflow_plugins", "examples.workflow_plugins.translate_query.nodes"
    )
    monkeypatch.setattr(settings, "workflow_definitions_dir", str(EXAMPLE_DIR))
    plugins.load_plugins(force=True)
    yield
    monkeypatch.undo()
    plugins.load_plugins(force=True)


def _core_digest() -> str:
    core = sorted((REPO / "backend" / "app" / "workflows").rglob("*.py"))
    return hashlib.sha1(b"".join(p.read_bytes() for p in core)).hexdigest()


async def test_translate_step_runs_before_retrieval(with_plugin):
    before = _core_digest()
    searcher = FakeSearcher()
    ctx = make_context(
        FirstCandidate(),
        searcher=searcher,
        agent_model=TestModel(
            custom_output_args={"query": "acetaminophen 500 mg tablet", "source_language": "hr"}
        ),
    )
    state = initial_state(
        source_id=1,
        source_value="X",
        source_name="paracetamol 500 mg tablete",
        domains=["Drug"],
        drug_specific=False,
        threshold=8,
    )
    result = await run_pipeline(state, ctx, "example_translated")

    assert [t["node"] for t in result["trace"]] == [
        "extract_atc7",
        "translate",
        "retrieve",
        "rerank",
        "gate",
    ]
    assert searcher.calls[0][0] == "acetaminophen 500 mg tablet"
    assert result["meta"]["original_query"] == "paracetamol 500 mg tablete"
    assert result["selected"]["concept_id"] == 3
    assert _core_digest() == before

    info = catalog()
    step = next(n for n in info["nodes"] if n["name"] == "translate_query")
    assert step["plugin"] == "examples.workflow_plugins.translate_query.nodes"
    assert "example_translated" in [p["name"] for p in info["pipelines"]]


def test_pipeline_unknown_without_plugin():
    plugins.load_plugins(force=True)
    with pytest.raises(builder.PipelineError):
        builder.get_pipeline("example_translated")
