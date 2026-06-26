"""Pure unit tests for the Trackly Query Language (TQL).

These exercise the tokenizer and the recursive-descent parser directly. Neither
needs a database; the only DB-touching path covered here is the unknown-sort-field
error in ``build_query``, which raises *before* any query is issued (the WHERE
clause is empty for an ORDER-BY-only query) and so can run with ``db=None``.
"""
from __future__ import annotations

import pytest

from app.services.tql import (
    BoolNode,
    Condition,
    Sort,
    TQLError,
    build_query,
    tokenize,
    Parser,
)


def _parse(text: str):
    return Parser(tokenize(text)).parse()


# ===========================================================================
# Tokenizer
# ===========================================================================
def test_tokenize_basic_condition():
    toks = tokenize("project = TEST")
    assert [(t.kind, t.value) for t in toks] == [
        ("word", "project"),
        ("op", "="),
        ("word", "TEST"),
    ]


def test_tokenize_quoted_string_strips_quotes():
    dq = tokenize('summary ~ "hello world"')
    assert dq[-1].kind == "string"
    assert dq[-1].value == "hello world"
    # Single quotes work too.
    sq = tokenize("summary ~ 'hi there'")
    assert sq[-1].kind == "string"
    assert sq[-1].value == "hi there"


def test_tokenize_keywords_are_uppercased_kinds():
    toks = tokenize("a = 1 and b = 2 order by c desc")
    kinds = [t.kind for t in toks]
    assert "AND" in kinds
    assert "ORDER" in kinds
    assert "BY" in kinds
    assert "DESC" in kinds


def test_tokenize_multichar_operators():
    for op in ("!=", ">=", "<=", "=", "~", ">", "<"):
        toks = tokenize(f"x {op} 1")
        assert toks[1].kind == "op"
        assert toks[1].value == op


def test_tokenize_in_and_not_keywords():
    toks = tokenize("status IN (Open, Closed)")
    kinds = [t.kind for t in toks]
    assert "IN" in kinds
    assert kinds.count("lparen") == 1
    assert kinds.count("rparen") == 1
    assert kinds.count("comma") == 1


# ===========================================================================
# Parser: conditions & operators
# ===========================================================================
def test_parse_simple_condition_ast():
    node, sorts = _parse("project = TEST")
    assert isinstance(node, Condition)
    assert node.field == "project"
    assert node.op == "="
    assert node.value == "TEST"
    assert sorts == []


def test_parse_quoted_value():
    node, _ = _parse('summary ~ "needs review"')
    assert isinstance(node, Condition)
    assert node.op == "~"
    assert node.value == "needs review"


@pytest.mark.parametrize("op", ["=", "!=", "~", ">", ">=", "<", "<="])
def test_parse_each_operator(op):
    node, _ = _parse(f"field {op} 5")
    assert isinstance(node, Condition)
    assert node.op == op
    assert node.value == "5"


def test_parse_in_list():
    node, _ = _parse("status IN (Open, Review, Closed)")
    assert isinstance(node, Condition)
    assert node.op == "IN"
    assert node.value == ["Open", "Review", "Closed"]


def test_parse_in_quoted_list_preserves_phrases():
    node, _ = _parse('status IN ("Open", "In Progress")')
    assert node.op == "IN"
    assert node.value == ["Open", "In Progress"]


def test_parse_not_in_list():
    node, _ = _parse("status NOT IN (Done, Closed)")
    assert isinstance(node, Condition)
    assert node.op == "NOT IN"
    assert node.value == ["Done", "Closed"]


def test_parse_in_single_bare_value():
    node, _ = _parse("project IN TEST")
    assert node.op == "IN"
    assert node.value == ["TEST"]


# ===========================================================================
# Parser: boolean composition & precedence
# ===========================================================================
def test_parse_and_or_left_associative():
    # a = 1 AND b = 2 OR c = 3  ->  ((a AND b) OR c)  (flat, left-assoc).
    node, _ = _parse("a = 1 AND b = 2 OR c = 3")
    assert isinstance(node, BoolNode)
    assert node.op == "OR"
    assert isinstance(node.left, BoolNode)
    assert node.left.op == "AND"
    assert isinstance(node.left.left, Condition) and node.left.left.field == "a"
    assert isinstance(node.left.right, Condition) and node.left.right.field == "b"
    assert isinstance(node.right, Condition) and node.right.field == "c"


def test_parse_parentheses_override_grouping():
    # (a = 1 OR b = 2) AND c = 3  ->  AND( OR(a,b), c )
    node, _ = _parse("(a = 1 OR b = 2) AND c = 3")
    assert isinstance(node, BoolNode)
    assert node.op == "AND"
    assert isinstance(node.left, BoolNode)
    assert node.left.op == "OR"
    assert isinstance(node.right, Condition)
    assert node.right.field == "c"


def test_parse_nested_parentheses():
    node, _ = _parse("((a = 1))")
    assert isinstance(node, Condition)
    assert node.field == "a"


# ===========================================================================
# Parser: ORDER BY
# ===========================================================================
def test_parse_order_by_single_default_asc():
    node, sorts = _parse("project = TEST ORDER BY created")
    assert isinstance(node, Condition)
    assert sorts == [Sort("created", "asc")]


def test_parse_order_by_multiple_with_directions():
    node, sorts = _parse("project = TEST ORDER BY created DESC, key ASC, summary")
    assert sorts == [
        Sort("created", "desc"),
        Sort("key", "asc"),
        Sort("summary", "asc"),
    ]


def test_parse_order_by_only_no_filter():
    node, sorts = _parse("ORDER BY updated DESC")
    assert node is None
    assert sorts == [Sort("updated", "desc")]


# ===========================================================================
# Parser: error cases
# ===========================================================================
def test_unbalanced_parens_raises():
    with pytest.raises(TQLError):
        _parse("(project = TEST")


def test_trailing_tokens_raise():
    with pytest.raises(TQLError):
        _parse("project = TEST extra = junk")


def test_missing_operator_raises():
    with pytest.raises(TQLError):
        _parse("project TEST")


def test_field_without_anything_raises():
    with pytest.raises(TQLError):
        _parse("project")


def test_order_without_by_raises():
    with pytest.raises(TQLError):
        _parse("project = TEST ORDER created")


def test_value_list_missing_comma_raises():
    with pytest.raises(TQLError):
        _parse("status IN (Open Closed")


# ===========================================================================
# build_query: empty + unknown-sort-field (no real DB needed)
# ===========================================================================
def test_build_query_empty_returns_default_order():
    where, order_by = build_query(None, "")
    assert where is None
    assert len(order_by) == 1  # default: updated_at DESC


def test_build_query_unknown_sort_field_raises():
    # An ORDER-BY-only query has an empty WHERE, so compilation never touches
    # the database; the unknown-sort-field check then fires with db=None.
    with pytest.raises(TQLError):
        build_query(None, "ORDER BY bogusfield")
