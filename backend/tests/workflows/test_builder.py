import pytest

from app.workflows.builder import PipelineError, compile_definition, parse_definition, validate_definition
from app.workflows.plugins import load_plugins

load_plugins()

VALID = """
name: ok
entry: a
nodes:
  a: { uses: extract_atc7 }
  b: { uses: retrieve, params: { k_default: 5 } }
  g: { uses: confidence_gate, routes: { accept: END, reject: END } }
edges:
  - [a, b]
  - [b, g]
"""


def test_valid_definition_compiles():
    compile_definition(parse_definition(VALID))


@pytest.mark.parametrize(
    "yaml_text,message",
    [
        (VALID.replace("uses: retrieve", "uses: does_not_exist"), "unknown step 'does_not_exist'"),
        (VALID.replace("k_default: 5", "k_default: 0"), "invalid params"),
        (VALID.replace("k_default: 5", "bogus: 1"), "invalid params"),
        (VALID.replace("{ accept: END, reject: END }", "{ accept: END }"), "must map exactly the routes"),
        (VALID.replace("{ accept: END, reject: END }", "{ accept: END, reject: nowhere }"), "unknown node 'nowhere'"),
        (VALID.replace("entry: a", "entry: zz"), "entry node 'zz'"),
        (VALID.replace("  - [b, g]\n", ""), "must have exactly one outgoing edge"),
        (VALID + "  - [b, a]\n", "must have exactly one outgoing edge"),
        (
            VALID.replace("  g: {", "  orphan: { uses: llm_rerank }\n  g: {") + "  - [orphan, END]\n",
            "unreachable nodes: ['orphan']",
        ),
        (VALID.replace("a: { uses: extract_atc7 }", "a: { uses: extract_atc7, routes: { x: END } }"), "not a router"),
    ],
)
def test_invalid_definitions(yaml_text, message):
    with pytest.raises(PipelineError, match=message.replace("[", r"\[").replace("]", r"\]")):
        validate_definition(parse_definition(yaml_text))


def test_cycle_without_end_is_rejected():
    text = """
name: loop
entry: a
nodes:
  a: { uses: extract_atc7 }
  b: { uses: llm_rerank }
edges:
  - [a, b]
  - [b, a]
"""
    with pytest.raises(PipelineError, match="no path from the entry node reaches END"):
        validate_definition(parse_definition(text))
