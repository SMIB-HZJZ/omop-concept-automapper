"""The auto-mapping progress line names the steps the terms in progress are at."""

import asyncio

from app.llm.structured import RerankResult
from app.worker.tasks import automap
from tests.workflows.helpers import make_context


class SlowReranker:
    async def rerank(self, term, candidates, drug_specific, **kwargs):
        await asyncio.sleep(2.6)  # longer than the 2 s refresh of the progress line
        return RerankResult(0, 9)


class Progress:
    def __init__(self):
        self.messages = []

    def update(self, current=None, total=None, message=None, **kwargs):
        if message:
            self.messages.append(message)

    def is_cancelled(self):
        return False


async def test_progress_line_shows_current_steps(monkeypatch):
    monkeypatch.setattr(automap, "persist_outcome", lambda state, **kwargs: "queued")
    progress = Progress()
    concepts = [
        {"source_id": i, "source_value": f"X{i}", "source_concept_name": name, "freq": 1}
        for i, name in enumerate(["metformin 500 mg", "acetaminophen 500 mg"], start=1)
    ]
    result = await automap.automap_concepts(
        concepts,
        make_context(SlowReranker()),
        domains=["Drug"],
        threshold=8,
        drug_specific=False,
        mode="review",
        pipeline="automap",
        progress=progress,
        task_id=None,
        user_id=None,
        concurrency=2,
    )
    assert result["queued"] == 2
    assert any("now: 2 AI pick" in m for m in progress.messages), progress.messages
    assert progress.messages[-1] == "2/2 processed · 2 queued for review"
