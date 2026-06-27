"""Trackly Query Language (TQL) — a small, original search DSL.

TQL is a deliberately simple expression language for filtering issues. It is an
independent implementation and shares no code with any other query language; it
merely follows the familiar `field operator value` shape common to search UIs.

Grammar (informal):

    query      := expr (ORDER BY sort (',' sort)*)?
    expr       := term (('AND' | 'OR') term)*
    term       := '(' expr ')' | condition
    condition  := field OP value
    OP         := '=' | '!=' | '~' | '>' | '>=' | '<' | '<=' | 'IN' | 'NOT IN'
    value      := bareword | quoted | '(' value (',' value)* ')'
    sort       := field ('ASC' | 'DESC')?

Supported fields: project, status, statusCategory, type, priority, assignee,
reporter, sprint, epic, parent, labels, resolution, summary, text, created,
updated, due, key.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import String, and_, asc, cast, desc, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import (
    Component,
    Issue,
    IssueType,
    Label,
    Priority,
    Project,
    Sprint,
    Status,
    User,
    issue_labels,
)


class TQLError(ValueError):
    """Raised when a query string cannot be parsed or a field is unknown."""


# --- Tokenizer -------------------------------------------------------------
_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<func>[A-Za-z_][A-Za-z_0-9]*\(\))
      | (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<comma>,)
      | (?P<op>!=|>=|<=|=|~|>|<)
      | (?P<string>"[^"]*"|'[^']*')
      | (?P<word>[^\s()\,]+)
    )
    """,
    re.VERBOSE,
)

_KEYWORDS = {"AND", "OR", "ORDER", "BY", "IN", "NOT", "ASC", "DESC"}


@dataclass
class Tok:
    kind: str
    value: str


def tokenize(text: str) -> list[Tok]:
    toks: list[Tok] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            raise TQLError(f"Unexpected character at position {pos}: {text[pos:pos+10]!r}")
        pos = m.end()
        kind = m.lastgroup
        val = m.group(kind)
        if kind == "string":
            toks.append(Tok("string", val[1:-1]))
        elif kind == "func":
            # Function tokens like currentUser() are treated as a single value word.
            toks.append(Tok("word", val))
        elif kind == "word":
            upper = val.upper()
            if upper in _KEYWORDS:
                toks.append(Tok(upper, upper))
            else:
                toks.append(Tok("word", val))
        else:
            toks.append(Tok(kind, val))
    return toks


# --- Parser (recursive descent) -------------------------------------------
@dataclass
class Condition:
    field: str
    op: str
    value: object


@dataclass
class BoolNode:
    op: str  # AND | OR
    left: object
    right: object


@dataclass
class Sort:
    field: str
    direction: str  # asc | desc


class Parser:
    def __init__(self, toks: list[Tok]):
        self.toks = toks
        self.i = 0

    def peek(self) -> Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self) -> Tok:
        tok = self.peek()
        if tok is None:
            raise TQLError("Unexpected end of query")
        self.i += 1
        return tok

    def parse(self) -> tuple[object | None, list[Sort]]:
        node = None
        if self.peek() and self.peek().kind != "ORDER":
            node = self.parse_expr()
        sorts: list[Sort] = []
        if self.peek() and self.peek().kind == "ORDER":
            self.next()
            if not self.peek() or self.peek().kind != "BY":
                raise TQLError("Expected BY after ORDER")
            self.next()
            sorts = self.parse_sorts()
        if self.peek() is not None:
            raise TQLError(f"Unexpected trailing token: {self.peek().value!r}")
        return node, sorts

    def parse_expr(self) -> object:
        node = self.parse_term()
        while self.peek() and self.peek().kind in ("AND", "OR"):
            op = self.next().kind
            right = self.parse_term()
            node = BoolNode(op, node, right)
        return node

    def parse_term(self) -> object:
        tok = self.peek()
        if tok and tok.kind == "lparen":
            self.next()
            node = self.parse_expr()
            if not self.peek() or self.peek().kind != "rparen":
                raise TQLError("Expected closing parenthesis")
            self.next()
            return node
        return self.parse_condition()

    def parse_condition(self) -> Condition:
        field_tok = self.next()
        if field_tok.kind not in ("word", "string"):
            raise TQLError(f"Expected field name, got {field_tok.value!r}")
        field = field_tok.value

        op_tok = self.next()
        if op_tok.kind == "op":
            op = op_tok.value
        elif op_tok.kind == "IN":
            op = "IN"
        elif op_tok.kind == "NOT":
            nxt = self.next()
            if nxt.kind != "IN":
                raise TQLError("Expected IN after NOT")
            op = "NOT IN"
        else:
            raise TQLError(f"Expected operator after field {field!r}, got {op_tok.value!r}")

        if op in ("IN", "NOT IN"):
            value = self.parse_list()
        else:
            value = self.parse_scalar()
        return Condition(field, op, value)

    def parse_scalar(self) -> str:
        tok = self.next()
        if tok.kind not in ("word", "string"):
            raise TQLError(f"Expected value, got {tok.value!r}")
        return tok.value

    def parse_list(self) -> list[str]:
        if not self.peek() or self.peek().kind != "lparen":
            # allow a single bare value after IN
            return [self.parse_scalar()]
        self.next()  # (
        values: list[str] = []
        while True:
            values.append(self.parse_scalar())
            nxt = self.next()
            if nxt.kind == "rparen":
                break
            if nxt.kind != "comma":
                raise TQLError("Expected ',' or ')' in value list")
        return values

    def parse_sorts(self) -> list[Sort]:
        sorts: list[Sort] = []
        while True:
            field = self.next().value
            direction = "asc"
            if self.peek() and self.peek().kind in ("ASC", "DESC"):
                direction = self.next().kind.lower()
            sorts.append(Sort(field, direction))
            if self.peek() and self.peek().kind == "comma":
                self.next()
                continue
            break
        return sorts


# --- Compiler: AST -> SQLAlchemy ------------------------------------------
_SORT_COLUMNS = {
    "key": Issue.key,
    "summary": Issue.summary,
    "created": Issue.created_at,
    "updated": Issue.updated_at,
    "due": Issue.due_date,
    "rank": Issue.rank,
    "storypoints": Issue.story_points,
    "points": Issue.story_points,
    "priority": Issue.priority_id,
    "status": Issue.status_id,
    "type": Issue.type_id,
    "assignee": Issue.assignee_id,
    "reporter": Issue.reporter_id,
}

_EMPTY = {"empty", "null", "none"}
# currentUser() with or without parens resolves to the requesting user.
_CURRENT_USER = {"currentuser()", "currentuser"}


class TQLCompiler:
    """Compiles a parsed TQL AST into a SQLAlchemy select() over Issue."""

    def __init__(self, db: Session):
        self.db = db

    # -- value resolution helpers (names -> ids) --
    def _project_ids(self, value: str) -> list[int]:
        q = select(Project.id).where(or_(Project.key.ilike(value), Project.name.ilike(value)))
        return list(self.db.scalars(q))

    def _status_ids(self, value: str) -> list[int]:
        return list(self.db.scalars(select(Status.id).where(Status.name.ilike(value))))

    def _type_ids(self, value: str) -> list[int]:
        return list(self.db.scalars(select(IssueType.id).where(IssueType.name.ilike(value))))

    def _priority_ids(self, value: str) -> list[int]:
        return list(self.db.scalars(select(Priority.id).where(Priority.name.ilike(value))))

    def _user_ids(self, value: str) -> list[int]:
        if value.lower() in _CURRENT_USER:
            return []  # resolved by caller via bound param; treated as empty here
        q = select(User.id).where(
            or_(User.username.ilike(value), User.email.ilike(value), User.display_name.ilike(value))
        )
        return list(self.db.scalars(q))

    def _sprint_ids(self, value: str) -> list[int]:
        if value.lower() == "active":
            return list(self.db.scalars(select(Sprint.id).where(Sprint.state == "active")))
        return list(self.db.scalars(select(Sprint.id).where(Sprint.name.ilike(value))))

    def compile_condition(self, c: Condition, current_user_id: int | None):
        field = c.field.lower()
        op = c.op
        val = c.value

        def in_clause(column, ids: list[int]):
            if op in ("=", "IN"):
                return column.in_(ids or [-1])
            return ~column.in_(ids or [-1])

        if field == "project":
            ids = [i for v in _as_list(val) for i in self._project_ids(v)]
            return in_clause(Issue.project_id, ids)
        if field in ("status",):
            ids = [i for v in _as_list(val) for i in self._status_ids(v)]
            return in_clause(Issue.status_id, ids)
        if field in ("statuscategory", "category"):
            cats = _as_list(val)
            sub = select(Status.id).where(Status.category.in_(cats))
            ids = list(self.db.scalars(sub))
            return in_clause(Issue.status_id, ids)
        if field in ("type", "issuetype"):
            ids = [i for v in _as_list(val) for i in self._type_ids(v)]
            return in_clause(Issue.type_id, ids)
        if field == "priority":
            ids = [i for v in _as_list(val) for i in self._priority_ids(v)]
            return in_clause(Issue.priority_id, ids)
        if field in ("assignee", "reporter"):
            col = Issue.assignee_id if field == "assignee" else Issue.reporter_id
            values = _as_list(val)
            if len(values) == 1 and values[0].lower() in _EMPTY:
                return col.is_(None) if op in ("=", "IN") else col.isnot(None)
            ids: list[int] = []
            for v in values:
                if v.lower() in _CURRENT_USER and current_user_id:
                    ids.append(current_user_id)
                else:
                    ids.extend(self._user_ids(v))
            return in_clause(col, ids)
        if field == "sprint":
            ids = [i for v in _as_list(val) for i in self._sprint_ids(v)]
            return in_clause(Issue.sprint_id, ids)
        if field in ("labels", "label"):
            values = _as_list(val)
            sub = (
                select(issue_labels.c.issue_id)
                .join(Label, Label.id == issue_labels.c.label_id)
                .where(Label.name.in_(values))
            )
            clause = Issue.id.in_(sub)
            return clause if op in ("=", "IN", "~") else ~clause
        if field in ("summary", "text", "description"):
            like = f"%{val}%"
            if field == "summary":
                return Issue.summary.ilike(like)
            return or_(Issue.summary.ilike(like), Issue.description.ilike(like))
        if field == "key":
            return Issue.key.ilike(val) if op in ("=", "~") else Issue.key.notilike(val)
        if field == "resolution":
            if str(val).lower() in _EMPTY:
                return Issue.resolution.is_(None) if op == "=" else Issue.resolution.isnot(None)
            return Issue.resolution.ilike(val) if op == "=" else Issue.resolution.notilike(val)
        if field in ("created", "updated", "due"):
            col = {"created": Issue.created_at, "updated": Issue.updated_at, "due": Issue.due_date}[field]
            return _date_clause(col, op, str(val))
        if field == "epic":
            sub = select(Issue.id).where(Issue.key.ilike(str(val)))
            return Issue.epic_id.in_(sub)
        if field == "parent":
            sub = select(Issue.id).where(Issue.key.ilike(str(val)))
            return Issue.parent_id.in_(sub)
        if field in ("storypoints", "points"):
            return _num_clause(Issue.story_points, op, str(val))
        raise TQLError(f"Unknown field: {c.field}")

    def compile_node(self, node, current_user_id: int | None):
        if node is None:
            return None
        if isinstance(node, BoolNode):
            left = self.compile_node(node.left, current_user_id)
            right = self.compile_node(node.right, current_user_id)
            return and_(left, right) if node.op == "AND" else or_(left, right)
        return self.compile_condition(node, current_user_id)


def _as_list(val) -> list[str]:
    return val if isinstance(val, list) else [val]


def _date_clause(col, op: str, val: str):
    from datetime import datetime, timedelta, timezone

    val = val.strip().lower()
    # relative: -7d, 1w etc.
    m = re.fullmatch(r"(-?\d+)([dwmh])", val)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = {"d": timedelta(days=n), "w": timedelta(weeks=n), "h": timedelta(hours=n), "m": timedelta(days=30 * n)}[unit]
        target = datetime.now(timezone.utc) + delta
    else:
        try:
            target = datetime.fromisoformat(val)
        except ValueError:
            raise TQLError(f"Invalid date value: {val!r}")
    ops = {">": col > target, ">=": col >= target, "<": col < target, "<=": col <= target, "=": col == target, "!=": col != target}
    if op not in ops:
        raise TQLError(f"Operator {op} not valid for dates")
    return ops[op]


def _num_clause(col, op: str, val: str):
    try:
        n = float(val)
    except ValueError:
        raise TQLError(f"Invalid number: {val!r}")
    ops = {">": col > n, ">=": col >= n, "<": col < n, "<=": col <= n, "=": col == n, "!=": col != n}
    if op not in ops:
        raise TQLError(f"Operator {op} not valid for numbers")
    return ops[op]


def build_query(db: Session, tql: str, current_user_id: int | None = None):
    """Parse *tql* and return (where_clause | None, order_by list).

    The caller composes these onto a base ``select(Issue)``.
    """
    tql = (tql or "").strip()
    if not tql:
        return None, [desc(Issue.updated_at)]
    node, sorts = Parser(tokenize(tql)).parse()
    compiler = TQLCompiler(db)
    where = compiler.compile_node(node, current_user_id)
    order_by = []
    for s in sorts:
        col = _SORT_COLUMNS.get(s.field.lower())
        if col is None:
            raise TQLError(f"Cannot sort by unknown field: {s.field}")
        order_by.append(desc(col) if s.direction == "desc" else asc(col))
    if not order_by:
        order_by = [desc(Issue.updated_at)]
    return where, order_by


# --- Schema/help catalog (drives UI autocomplete + examples) ---------------
_EQ = ["=", "!=", "IN", "NOT IN"]
_DATE_OPS = [">", ">=", "<", "<=", "=", "!="]

TQL_FIELDS = [
    {"name": "project", "type": "option", "operators": _EQ, "values": True, "description": "Project key or name"},
    {"name": "status", "type": "option", "operators": _EQ, "values": True, "description": "Workflow status, e.g. \"In Progress\""},
    {"name": "statusCategory", "type": "option", "operators": _EQ, "values": True, "description": "todo | in_progress | done"},
    {"name": "type", "type": "option", "operators": _EQ, "values": True, "description": "Issue type, e.g. Bug, Story"},
    {"name": "priority", "type": "option", "operators": _EQ, "values": True, "description": "Highest, High, Medium, Low, Lowest"},
    {"name": "assignee", "type": "user", "operators": _EQ, "values": True, "description": "User; use currentUser() or empty"},
    {"name": "reporter", "type": "user", "operators": _EQ, "values": True, "description": "User; use currentUser() or empty"},
    {"name": "sprint", "type": "option", "operators": _EQ, "values": True, "description": "Sprint name, or active"},
    {"name": "labels", "type": "option", "operators": ["=", "!=", "~", "IN"], "values": True, "description": "A label"},
    {"name": "resolution", "type": "option", "operators": ["=", "!="], "values": True, "description": "Resolution, or empty (unresolved)"},
    {"name": "epic", "type": "text", "operators": ["="], "values": False, "description": "Epic issue key"},
    {"name": "parent", "type": "text", "operators": ["="], "values": False, "description": "Parent issue key"},
    {"name": "summary", "type": "text", "operators": ["~", "="], "values": False, "description": "Text in the summary"},
    {"name": "text", "type": "text", "operators": ["~"], "values": False, "description": "Text in summary or description"},
    {"name": "key", "type": "text", "operators": ["=", "~"], "values": False, "description": "Issue key, e.g. ENG-12"},
    {"name": "created", "type": "date", "operators": _DATE_OPS, "values": False, "description": "Created date: -7d, -2w, or YYYY-MM-DD"},
    {"name": "updated", "type": "date", "operators": _DATE_OPS, "values": False, "description": "Last updated: -7d, or YYYY-MM-DD"},
    {"name": "due", "type": "date", "operators": _DATE_OPS, "values": False, "description": "Due date: 0d (today), -7d, YYYY-MM-DD"},
    {"name": "storyPoints", "type": "number", "operators": _DATE_OPS, "values": False, "description": "Story points, e.g. >= 5"},
]

TQL_KEYWORDS = ["AND", "OR", "ORDER BY", "ASC", "DESC", "IN", "NOT IN"]
TQL_FUNCTIONS = ["currentUser()"]
TQL_SPECIALS = ["empty"]

TQL_EXAMPLES = [
    {"label": "My open work", "query": "assignee = currentUser() AND statusCategory != done ORDER BY updated DESC"},
    {"label": "Open bugs by priority", "query": "type = Bug AND statusCategory != done ORDER BY priority ASC"},
    {"label": "High priority in a project", "query": "project = ENG AND priority IN (High, Highest)"},
    {"label": "Updated in the last week", "query": "updated >= -7d ORDER BY updated DESC"},
    {"label": "Unassigned, in progress", "query": "assignee = empty AND statusCategory = in_progress"},
    {"label": "Overdue and not done", "query": "due < 0d AND statusCategory != done ORDER BY due ASC"},
    {"label": "By label", "query": "labels = backend AND status = \"In Progress\""},
]


def tql_schema() -> dict:
    """Static catalog of fields, operators, keywords, functions and examples,
    used by the UI to power autocomplete and the help/examples panel."""
    return {
        "fields": TQL_FIELDS,
        "keywords": TQL_KEYWORDS,
        "functions": TQL_FUNCTIONS,
        "specials": TQL_SPECIALS,
        "examples": TQL_EXAMPLES,
    }
