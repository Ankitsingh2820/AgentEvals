import json

import pytest

from app.tools import get_tool, unknown_tools


def test_calculator_evaluates_arithmetic():
    assert get_tool("calculator").fn(expression="(12 + 3) * 4") == "60"
    assert get_tool("calculator").fn(expression="-2 ** 2") == "-4"


@pytest.mark.parametrize("expr", ["__import__('os')", "a + 1", "2 ** 1000", "[1, 2]"])
def test_calculator_rejects_unsafe_or_unsupported(expr):
    with pytest.raises(ValueError):
        get_tool("calculator").fn(expression=expr)


def test_company_lookup_is_case_insensitive():
    profile = json.loads(get_tool("company_lookup").fn(name="  acme corp "))
    assert profile["industry"] == "Industrial Automation"


def test_company_lookup_unknown_company_raises():
    with pytest.raises(LookupError):
        get_tool("company_lookup").fn(name="Nonexistent Inc")


def test_unknown_tools():
    assert unknown_tools(["calculator", "nope"]) == ["nope"]
