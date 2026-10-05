"""Registry of workflow steps.

Nodes and routers register themselves with a name; pipeline definitions (YAML) refer to them
by that name. Third-party steps register the same way from plugin modules, so new steps can
be added without touching the core.

    @node("my_step", params=MyParams, description="…")
    async def my_step(state: AutomapState, ctx: NodeContext, params: MyParams) -> dict:
        return {"query": ..., "_summary": "what happened"}

    @router("my_gate", routes={"yes", "no"})
    def my_gate(state, ctx, params) -> str | tuple[str, dict]:
        return "yes"
"""

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class StepSpec:
    name: str
    fn: Callable[..., Any]
    kind: Literal["node", "router"]
    params_model: type[BaseModel] = NoParams
    description: str | None = None
    routes: frozenset[str] = field(default_factory=frozenset)
    plugin: str | None = None


_REGISTRY: dict[str, StepSpec] = {}
current_plugin: ContextVar[str | None] = ContextVar("current_plugin", default=None)


class RegistryError(ValueError):
    pass


def _register(spec: StepSpec) -> None:
    existing = _REGISTRY.get(spec.name)
    if existing and existing.fn is not spec.fn and existing.fn.__qualname__ != spec.fn.__qualname__:
        raise RegistryError(f"A workflow step named {spec.name!r} is already registered")
    _REGISTRY[spec.name] = spec


def node(name: str, *, params: type[BaseModel] = NoParams, description: str | None = None):
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _register(
            StepSpec(
                name=name,
                fn=fn,
                kind="node",
                params_model=params,
                description=description or (fn.__doc__ or "").strip().split("\n")[0] or None,
                plugin=current_plugin.get(),
            )
        )
        return fn

    return decorator


def router(
    name: str,
    *,
    routes: set[str],
    params: type[BaseModel] = NoParams,
    description: str | None = None,
):
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _register(
            StepSpec(
                name=name,
                fn=fn,
                kind="router",
                params_model=params,
                description=description or (fn.__doc__ or "").strip().split("\n")[0] or None,
                routes=frozenset(routes),
                plugin=current_plugin.get(),
            )
        )
        return fn

    return decorator


def get_step(name: str) -> StepSpec | None:
    return _REGISTRY.get(name)


def all_steps() -> list[StepSpec]:
    return sorted(_REGISTRY.values(), key=lambda s: s.name)


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)
