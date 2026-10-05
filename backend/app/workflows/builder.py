"""Loads declarative pipeline definitions (YAML), validates them and compiles LangGraph graphs.

A definition names registered steps and wires them together:

    name: automap
    entry: extract_atc7
    nodes:
      extract_atc7: { uses: extract_atc7 }
      retrieve:     { uses: retrieve, params: { k_default: 15 } }
      gate:         { uses: confidence_gate, routes: { accept: END, reject: END } }
    edges:
      - [extract_atc7, retrieve]
      - [retrieve, gate]

Adding, removing or reordering steps and decision points is a YAML change; new step types come
from plugins (see ``registry.py``). This module and the callers never change.
"""

import hashlib
import inspect
import json
import time
from collections import deque
from pathlib import Path
from typing import Any

import yaml
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import get_settings
from app.workflows.context import NodeContext
from app.workflows.plugins import load_plugins
from app.workflows.registry import StepSpec, get_step
from app.workflows.state import AutomapState

BUILTIN_DIR = Path(__file__).parent / "definitions"
END_NAME = "END"


class NodeRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uses: str
    params: dict[str, Any] = Field(default_factory=dict)
    routes: dict[str, str] | None = None


class PipelineDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9_\-]+$")
    description: str | None = None
    entry: str
    nodes: dict[str, NodeRef]
    edges: list[tuple[str, str]] = Field(default_factory=list)


class PipelineError(ValueError):
    pass


# --------------------------------------------------------------------------- loading


def definition_dirs() -> list[Path]:
    dirs = [BUILTIN_DIR]
    extra = get_settings().workflow_definitions_dir
    for item in (extra or "").split(","):
        if item.strip():
            dirs.append(Path(item.strip()))
    return dirs


def load_definitions() -> tuple[dict[str, tuple[PipelineDefinition, str]], dict[str, str]]:
    """All pipeline definitions found on disk, plus per-file errors. Later dirs override earlier ones."""
    load_plugins()
    found: dict[str, tuple[PipelineDefinition, str]] = {}
    errors: dict[str, str] = {}
    for directory in definition_dirs():
        if not directory.is_dir():
            if directory != BUILTIN_DIR:
                errors[str(directory)] = "definition directory not found"
            continue
        for path in sorted(directory.glob("*.y*ml")):
            try:
                definition = parse_definition(path.read_text())
                validate_definition(definition)
                found[definition.name] = (definition, str(path))
            except (PipelineError, ValidationError, yaml.YAMLError) as exc:
                errors[str(path)] = str(exc)
    return found, errors


def parse_definition(text: str) -> PipelineDefinition:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise PipelineError("A pipeline definition must be a YAML mapping")
    return PipelineDefinition.model_validate(data)


# --------------------------------------------------------------------------- validation


def validate_definition(d: PipelineDefinition) -> dict[str, tuple[StepSpec, BaseModel]]:
    """Check a definition against the registry; returns the resolved steps and parsed params."""
    problems: list[str] = []
    resolved: dict[str, tuple[StepSpec, BaseModel]] = {}

    if END_NAME in d.nodes:
        problems.append("'END' is reserved and cannot be used as a node id")
    if d.entry not in d.nodes:
        problems.append(f"entry node {d.entry!r} is not defined")

    for node_id, ref in d.nodes.items():
        spec = get_step(ref.uses)
        if spec is None:
            problems.append(f"node {node_id!r} uses unknown step {ref.uses!r}")
            continue
        try:
            params = spec.params_model.model_validate(ref.params)
        except ValidationError as exc:
            problems.append(f"node {node_id!r}: invalid params for {ref.uses!r}: {exc.errors()}")
            continue
        if spec.kind == "router":
            if not ref.routes:
                problems.append(f"router node {node_id!r} needs 'routes' for {sorted(spec.routes)}")
            else:
                declared, given = set(spec.routes), set(ref.routes)
                if declared != given:
                    problems.append(
                        f"router node {node_id!r} must map exactly the routes {sorted(declared)}, got {sorted(given)}"
                    )
                for label, target in ref.routes.items():
                    if target != END_NAME and target not in d.nodes:
                        problems.append(
                            f"route {node_id}.{label} points to unknown node {target!r}"
                        )
        elif ref.routes:
            problems.append(f"node {node_id!r} is not a router and cannot declare routes")
        resolved[node_id] = (spec, params)

    outgoing: dict[str, list[str]] = {n: [] for n in d.nodes}
    for src, dst in d.edges:
        if src not in d.nodes:
            problems.append(f"edge from unknown node {src!r}")
            continue
        if dst != END_NAME and dst not in d.nodes:
            problems.append(f"edge to unknown node {dst!r}")
            continue
        outgoing[src].append(dst)

    for node_id, ref in d.nodes.items():
        spec = resolved.get(node_id, (None, None))[0]
        if spec is None:
            continue
        if spec.kind == "router":
            if outgoing[node_id]:
                problems.append(f"router node {node_id!r} cannot also have plain edges")
            outgoing[node_id] = list((ref.routes or {}).values())
        elif len(outgoing[node_id]) != 1:
            problems.append(
                f"node {node_id!r} must have exactly one outgoing edge (use END to finish), "
                f"has {len(outgoing[node_id])}"
            )

    if not problems:
        reachable, reaches_end = set(), False
        queue = deque([d.entry])
        while queue:
            current = queue.popleft()
            if current == END_NAME:
                reaches_end = True
                continue
            if current in reachable:
                continue
            reachable.add(current)
            queue.extend(outgoing.get(current, []))
        unreachable = sorted(set(d.nodes) - reachable)
        if unreachable:
            problems.append(f"unreachable nodes: {unreachable}")
        if not reaches_end:
            problems.append("no path from the entry node reaches END")

    if problems:
        raise PipelineError(f"pipeline {d.name!r} is invalid: " + "; ".join(problems))
    return resolved


# --------------------------------------------------------------------------- compiling


def _target(name: str) -> str:
    return END if name == END_NAME else name


async def _call(fn, *args):
    result = fn(*args)
    if inspect.isawaitable(result):
        result = await result
    return result


def _wrap_node(node_id: str, spec: StepSpec, params: BaseModel):
    async def run(state: AutomapState, runtime: Runtime[NodeContext]) -> dict:
        started = time.perf_counter()
        update = dict(await _call(spec.fn, state, runtime.context, params) or {})
        summary = str(update.pop("_summary", ""))
        update["trace"] = [
            {
                "node": node_id,
                "ms": round((time.perf_counter() - started) * 1000, 1),
                "summary": summary,
            }
        ]
        return update

    run.__name__ = f"node_{node_id}"
    return run


def _wrap_router(node_id: str, spec: StepSpec, params: BaseModel):
    async def run(state: AutomapState, runtime: Runtime[NodeContext]) -> dict:
        started = time.perf_counter()
        result = await _call(spec.fn, state, runtime.context, params)
        label, update = (result, {}) if isinstance(result, str) else result
        if label not in spec.routes:
            raise PipelineError(f"router {spec.name!r} returned unknown route {label!r}")
        update = dict(update or {})
        summary = str(update.pop("_summary", ""))
        update["route"] = label
        update["trace"] = [
            {
                "node": node_id,
                "ms": round((time.perf_counter() - started) * 1000, 1),
                "summary": f"→ {label}" + (f" ({summary})" if summary else ""),
            }
        ]
        return update

    run.__name__ = f"router_{node_id}"
    return run


def compile_definition(d: PipelineDefinition):
    resolved = validate_definition(d)
    graph = StateGraph(AutomapState, context_schema=NodeContext)
    for node_id, (spec, params) in resolved.items():
        wrapper = _wrap_router if spec.kind == "router" else _wrap_node
        graph.add_node(node_id, wrapper(node_id, spec, params))
    graph.add_edge(START, d.entry)
    for src, dst in d.edges:
        graph.add_edge(src, _target(dst))
    for node_id, ref in d.nodes.items():
        if ref.routes:
            mapping = {label: _target(target) for label, target in ref.routes.items()}
            graph.add_conditional_edges(node_id, lambda s: s["route"], mapping)
    return graph.compile()


_compiled: dict[str, Any] = {}


def get_pipeline(name: str | None = None):
    """Compiled graph for a pipeline name (default: AUTOMAP_PIPELINE), cached per definition."""
    name = name or get_settings().automap_pipeline
    definitions, errors = load_definitions()
    if name not in definitions:
        hint = next((err for path, err in errors.items() if Path(path).stem == name), None)
        raise PipelineError(f"Unknown pipeline {name!r}" + (f": {hint}" if hint else ""))
    definition, _ = definitions[name]
    key = (
        name
        + ":"
        + hashlib.sha1(json.dumps(definition.model_dump(), sort_keys=True).encode()).hexdigest()
    )
    if key not in _compiled:
        _compiled[key] = compile_definition(definition)
    return _compiled[key], definition
