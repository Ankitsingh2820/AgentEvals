"""Tool registry. Agents reference tools by name; the engine looks them up here.

A tool is a plain function taking keyword arguments and returning a string. Its JSON
schema is what the model sees. Raising an exception marks the call as failed and the
error text is returned to the model as an error result.
"""

import ast
import json
import operator
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.providers.base import ToolSpec


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    fn: Callable[..., str]


_registry: dict[str, Tool] = {}


def register_tool(name: str, description: str, input_schema: dict[str, Any]):
    def decorator(fn: Callable[..., str]) -> Callable[..., str]:
        _registry[name] = Tool(ToolSpec(name, description, input_schema), fn)
        return fn

    return decorator


def get_tool(name: str) -> Tool:
    return _registry[name]


def available_tools() -> list[ToolSpec]:
    return [t.spec for t in _registry.values()]


def unknown_tools(names: list[str]) -> list[str]:
    return [n for n in names if n not in _registry]


# --- Built-in tools -------------------------------------------------------------------

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        if isinstance(node.op, ast.Pow) and abs(_eval_node(node.right)) > 100:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("unsupported expression")


@register_tool(
    "calculator",
    "Evaluate an arithmetic expression (+ - * / % ** and parentheses). Returns the number.",
    {
        "type": "object",
        "properties": {"expression": {"type": "string", "description": "e.g. (12 + 3) * 4"}},
        "required": ["expression"],
    },
)
def calculator(expression: str) -> str:
    return str(_eval_node(ast.parse(expression, mode="eval").body))


_COMPANIES_FILE = Path(__file__).parent / "data" / "companies.json"


def _companies() -> dict[str, dict[str, Any]]:
    data = json.loads(_COMPANIES_FILE.read_text(encoding="utf-8"))
    return {c["name"].lower(): c for c in data}


@register_tool(
    "company_lookup",
    "Look up a company in the internal company database by name. Returns its profile "
    "(description, industry, employee count, headquarters) as JSON.",
    {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Company name"}},
        "required": ["name"],
    },
)
def company_lookup(name: str) -> str:
    company = _companies().get(name.strip().lower())
    if company is None:
        raise LookupError(f"no company named '{name}' in the database")
    return json.dumps(company)
