"""Read-only SQL migration/DDL parser (Structure S2).

Implements the S1 observation contract (python/structure/contract.py) for a
bounded PostgreSQL migration/DDL subset, per to-do thread 9a7eab15 and
architect ruling 143b0e04 (discussions thread 2ecc4700):

- Read-only: no I/O, no database, no writes to Aspects/Resolution/PEB/graph.
  The module only turns SQL text into observation dicts.
- Deterministic: no clocks, no randomness, no dict-order dependence; output
  is a pure function of (text, source identity, pinned revisions).
- Honest: procedural bodies, dynamic SQL and other out-of-grammar constructs
  produce parse_status="unsupported"/"partial" observations WITH diagnostics.
  Absence of a fact never asserts non-existence.
- Fully accounted: every statement yields at least one observation, including
  unsupported ones, so a run never silently drops input.

Capability profile is explicit (CAPABILITY_PROFILE): PL/pgSQL bodies, dynamic
SQL, and extension syntax are named as unsupported, not assumed.

Bounded grammar (grammar_revision SQL-DDL-v0.2.0):
  CREATE TABLE (columns, PK/UNIQUE/CHECK/FK constraints, column REFERENCES)
  ALTER TABLE  (ADD COLUMN/CONSTRAINT, DROP COLUMN, other actions as operations)
  CREATE [UNIQUE] INDEX
  CREATE TYPE ... AS ENUM
  INSERT INTO ... VALUES (seed_value facts per tuple) / ... SELECT (partial)
  everything else (CREATE FUNCTION, DO, EXECUTE, ...) -> explicit unsupported
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

try:
    from . import contract as sc  # package context
except ImportError:  # pragma: no cover - direct-run / test harness context
    import contract as sc  # type: ignore[no-redef]

PARSER_IDENTITY = "structure-sql-parser"
PARSER_REVISION = "v0.2.0"
GRAMMAR_REVISION = "sql-ddl-v0.2.0"

CAPABILITY_PROFILE = {
    "dialect": "postgresql",
    "grammar_revision": GRAMMAR_REVISION,
    "statement_types_covered": [
        "create_table",
        "alter_table",
        "create_index",
        "create_type_as_enum",
        "insert_values",
        "insert_select",
    ],
    "explicitly_unsupported": [
        "plpgsql_procedural_bodies",
        "dynamic_sql_execute",
        "extension_syntax",
        "triggers_and_rules",
        "views_and_matviews",
        "functions_and_procedures",
    ],
    "comment_forms": ["line", "block_nested"],
    "string_forms": ["single_quote_escaped", "dollar_quoted_tags"],
    "case_policy": "keyword matching is ASCII case-insensitive; identifiers preserved",
}

_PAYLOAD_CAP = 256

# Keywords that terminate a column *type* and begin a column constraint.
_COL_CONSTRAINT_KEYWORDS = {
    "primary", "unique", "not", "null", "default", "references",
    "check", "generated", "collate", "constraint",
}
# Constraint keywords that may follow DEFAULT inside a column definition.
_POST_DEFAULT_KEYWORDS = _COL_CONSTRAINT_KEYWORDS - {"default", "null"}

_PUNCT_NO_SPACE_BEFORE = {",", ")", ".", ";", "("}
_PUNCT_NO_SPACE_AFTER = {"(", "."}


# ---------------------------------------------------------------------------
# Tokenizer


@dataclass
class Token:
    kind: str  # word|qident|string|dollar|number|punct
    text: str  # token text; for string/dollar: content without quotes
    line: int  # 1-based
    col: int  # 1-based
    end_line: int
    end_col: int  # exclusive


def tokenize(sql: str) -> list[Token]:
    """Tokenize SQL with honest handling of comments, strings, dollar quotes.

    Comments are skipped (they cannot yield facts); block comments nest.
    Dollar-quoted bodies (e.g. PL/pgSQL function bodies) become one string
    token — which is exactly what keeps a function body's internal
    semicolons from splitting statements.
    """
    tokens: list[Token] = []
    i, n = 0, len(sql)
    line, col = 1, 1

    def advance(ch: str) -> None:
        nonlocal line, col
        if ch == "\n":
            line += 1
            col = 1
        else:
            col += 1

    while i < n:
        ch = sql[i]
        if ch in " \t\r\n":
            advance(ch)
            i += 1
            continue
        start_line, start_col = line, col
        # line comment
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                advance(sql[i])
                i += 1
            continue
        # nested block comment
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            depth = 0
            while i < n:
                if sql.startswith("/*", i):
                    depth += 1
                    advance(sql[i])
                    advance(sql[i + 1])
                    i += 2
                elif sql.startswith("*/", i):
                    depth -= 1
                    advance(sql[i])
                    advance(sql[i + 1])
                    i += 2
                    if depth == 0:
                        break
                else:
                    advance(sql[i])
                    i += 1
            continue
        # single-quoted string ('' escape)
        if ch == "'":
            i += 1
            advance("'")
            buf: list[str] = []
            while i < n:
                if sql[i] == "'" and i + 1 < n and sql[i + 1] == "'":
                    buf.append("'")
                    advance(sql[i])
                    advance(sql[i + 1])
                    i += 2
                elif sql[i] == "'":
                    advance(sql[i])
                    i += 1
                    break
                else:
                    buf.append(sql[i])
                    advance(sql[i])
                    i += 1
            tokens.append(Token("string", "".join(buf), start_line, start_col, line, col))
            continue
        # dollar-quoted string $tag$...$tag$
        if ch == "$":
            m = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$", sql[i:])
            if m:
                tag = m.group(0)
                close = sql.find(tag, i + len(tag))
                if close != -1:
                    body = sql[i + len(tag):close]
                    for c in sql[i:close + len(tag)]:
                        advance(c)
                    i = close + len(tag)
                    tokens.append(Token("dollar", body, start_line, start_col, line, col))
                    continue
            tokens.append(Token("punct", "$", start_line, start_col, line, col + 1))
            advance(ch)
            i += 1
            continue
        # quoted identifier
        if ch == '"':
            i += 1
            advance('"')
            buf = []
            while i < n:
                if sql[i] == '"' and i + 1 < n and sql[i + 1] == '"':
                    buf.append('"')
                    advance(sql[i])
                    advance(sql[i + 1])
                    i += 2
                elif sql[i] == '"':
                    advance(sql[i])
                    i += 1
                    break
                else:
                    buf.append(sql[i])
                    advance(sql[i])
                    i += 1
            tokens.append(Token("qident", "".join(buf), start_line, start_col, line, col))
            continue
        # word
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                advance(sql[j])
                j += 1
            tokens.append(Token("word", sql[i:j], start_line, start_col, line, col))
            i = j
            continue
        # number
        if ch.isdigit():
            j = i
            while j < n and (sql[j].isalnum() or sql[j] in "._"):
                advance(sql[j])
                j += 1
            tokens.append(Token("number", sql[i:j], start_line, start_col, line, col))
            i = j
            continue
        # punctuation (single char)
        tokens.append(Token("punct", ch, start_line, start_col, line, col + 1))
        advance(ch)
        i += 1
    return tokens


# ---------------------------------------------------------------------------
# Statement splitting


@dataclass
class Statement:
    index: int
    tokens: list[Token]
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    terminated: bool  # False for trailing content without ';'


def split_statements(tokens: list[Token]) -> list[Statement]:
    statements: list[Statement] = []
    cur: list[Token] = []

    def flush(last: Token | None, terminated: bool) -> None:
        if not cur:
            return
        statements.append(Statement(
            index=len(statements),
            tokens=list(cur),
            start_line=cur[0].line,
            start_col=cur[0].col,
            end_line=(last or cur[-1]).end_line,
            end_col=(last or cur[-1]).end_col,
            terminated=terminated,
        ))
        cur.clear()

    for tok in tokens:
        if tok.kind == "punct" and tok.text == ";":
            flush(tok, True)
        else:
            cur.append(tok)
    flush(None, False)
    return statements


# ---------------------------------------------------------------------------
# Token helpers


def _kw(tok: Token) -> str:
    return tok.text.lower() if tok.kind == "word" else ""


def _render(tokens: list[Token]) -> str:
    """Deterministic, readable reconstruction of token text."""
    out = ""
    prev_kind = ""
    prev_text = ""
    for t in tokens:
        if t.kind == "string":
            text = "'" + t.text.replace("'", "''") + "'"
        elif t.kind == "dollar":
            text = "$…$"
        else:
            text = t.text
        if not out:
            out = text
        elif (t.kind == "punct" and text in _PUNCT_NO_SPACE_BEFORE) or (
            prev_kind == "punct" and prev_text in _PUNCT_NO_SPACE_AFTER
        ):
            out += text
        else:
            out += " " + text
        prev_kind, prev_text = t.kind, text
    return out


def _qualified_name(tokens: list[Token], i: int) -> tuple[str, int]:
    """Parse a possibly schema-qualified name at tokens[i]; return (name, next_i)."""
    if i >= len(tokens) or tokens[i].kind not in ("word", "qident"):
        return "", i
    parts = [tokens[i].text]
    i += 1
    while i + 1 < len(tokens) and tokens[i].kind == "punct" and tokens[i].text == ".":
        if tokens[i + 1].kind not in ("word", "qident"):
            break
        parts.append(tokens[i + 1].text)
        i += 2
    return ".".join(parts), i


def _split_top_level(tokens: list[Token], sep: str = ",") -> list[list[Token]]:
    parts: list[list[Token]] = []
    depth = 0
    cur: list[Token] = []
    for t in tokens:
        if t.kind == "punct":
            if t.text == "(":
                depth += 1
            elif t.text == ")":
                depth -= 1
            elif t.text == sep and depth == 0:
                parts.append(cur)
                cur = []
                continue
        cur.append(t)
    if cur:
        parts.append(cur)
    return parts


def _paren_body(tokens: list[Token]) -> tuple[list[Token], int] | None:
    """If tokens[0] is '(', return (inner tokens, index after matching ')')."""
    if not tokens or tokens[0].text != "(":
        return None
    depth = 0
    for j, t in enumerate(tokens):
        if t.kind == "punct":
            if t.text == "(":
                depth += 1
            elif t.text == ")":
                depth -= 1
                if depth == 0:
                    return tokens[1:j], j + 1
    return None


def _cap(text: str) -> str:
    if len(text) <= _PAYLOAD_CAP:
        return text
    return text[:_PAYLOAD_CAP] + "…[truncated]"


def _scalar(tok: Token) -> Any:
    if tok.kind == "string":
        return tok.text
    if tok.kind == "number":
        return tok.text
    if tok.kind == "dollar":
        return {"dollar_tag": True, "raw": _cap(tok.text)}
    if tok.kind == "word" and tok.text.upper() == "NULL":
        return None
    return {"raw": tok.text}


def _scalar_group(group: list[Token]) -> Any:
    """Scalar value from a token group; multi-token values render verbatim."""
    if not group:
        return None
    if len(group) == 1:
        return _scalar(group[0])
    return {"raw": _cap(_render(group))}


def _loc(tokens: list[Token]) -> dict[str, int]:
    return {
        "start_line": tokens[0].line,
        "start_col": tokens[0].col,
        "end_line": tokens[-1].end_line,
        "end_col": tokens[-1].end_col,
    }


def _path(stmt: Statement, *parts: str) -> str:
    return f"statements[{stmt.index}]." + ".".join(p for p in parts if p)


def _upper_seq(tokens: list[Token]) -> list[str]:
    return [t.text.upper() if t.kind == "word" else "" for t in tokens]


# ---------------------------------------------------------------------------
# Grammar: foreign keys, columns, constraints


def _parse_fk(tokens: list[Token]) -> dict[str, Any] | None:
    """Parse 'FOREIGN KEY (cols) REFERENCES tbl [(cols)] [...]' if present."""
    upper = _upper_seq(tokens)
    if "REFERENCES" not in upper:
        return None
    ri = upper.index("REFERENCES")
    payload: dict[str, Any] = {}
    if "FOREIGN" in upper:
        fi = upper.index("FOREIGN")
        if fi + 1 < len(tokens) and _kw(tokens[fi + 1]) == "key":
            body = _paren_body(tokens[fi + 2:])
            if body:
                payload["columns"] = [_render(p) for p in _split_top_level(body[0])]
    ref_name, ni = _qualified_name(tokens, ri + 1)
    payload["references_table"] = ref_name
    if ni < len(tokens) and tokens[ni].text == "(":
        body = _paren_body(tokens[ni:])
        if body:
            payload["references_columns"] = [
                _render(p) for p in _split_top_level(body[0])
            ]
            ni = body[1]
    for key in ("ON DELETE", "ON UPDATE"):
        parts = key.split()
        for pi in range(len(upper) - 1):
            if upper[pi] == parts[0] and upper[pi + 1] == parts[1]:
                payload[key.replace(" ", "_").lower()] = (
                    tokens[pi + 2].text.upper() if pi + 2 < len(tokens) else None
                )
                break
    return payload


def _column_observation(
    stmt: Statement, table: str, part: list[Token], *, path_suffix: str
) -> dict[str, Any] | None:
    """Parse one column definition (without inline FK — see _column_with_fk)."""
    if not part or part[0].kind not in ("word", "qident"):
        return None
    name_tok = part[0]
    type_end = len(part)
    for j in range(1, len(part)):
        t = part[j]
        if t.kind == "word" and t.text.lower() in _COL_CONSTRAINT_KEYWORDS:
            type_end = j
            break
    type_tokens = part[1:type_end]
    payload: dict[str, Any] = {
        "table": table,
        "name": name_tok.text,
        "type": _render(type_tokens),
        "statement_index": stmt.index,
    }
    upper = _upper_seq(part)
    for u in range(len(upper) - 1):
        if upper[u] == "NOT" and upper[u + 1] == "NULL":
            payload["nullable"] = False
            break
    if "UNIQUE" in upper:
        payload["unique"] = True
    if "PRIMARY" in upper:
        payload["primary_key"] = True
    if "DEFAULT" in upper:
        di = upper.index("DEFAULT")
        nj = di + 1
        while nj < len(part) and not (
            part[nj].kind == "word"
            and part[nj].text.lower() in _POST_DEFAULT_KEYWORDS
        ):
            nj += 1
        payload["default"] = _scalar_group(part[di + 1:nj])
    return {
        "fact_kind": "column",
        "payload": payload,
        "anchor": {
            "node_path": _path(stmt, "table", table.split(".")[-1], path_suffix),
            "span": _loc(part),
        },
    }


def _column_with_fk(
    stmt: Statement, table: str, part: list[Token], *, path_suffix: str, fk_index: int
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Column definition plus optional inline REFERENCES clause."""
    col = _column_observation(stmt, table, part, path_suffix=path_suffix)
    fk = _parse_fk(part)
    if not fk:
        return col, None
    fk_payload = {
        "table": table,
        "columns": fk.get("columns") or ([part[0].text] if part else []),
        "references_table": fk.get("references_table"),
        "references_columns": fk.get("references_columns"),
        "on_delete": fk.get("on_delete"),
        "on_update": fk.get("on_update"),
        "constraint_name": None,
        "statement_index": stmt.index,
    }
    fk_obs = {
        "fact_kind": "foreign_key",
        "payload": fk_payload,
        "anchor": {
            "node_path": _path(stmt, "table", table.split(".")[-1], f"fks[{fk_index}]"),
            "span": _loc(part),
        },
        "relation_mapping": {"status": "unmapped"},
    }
    return col, fk_obs


def _constraint_observation(
    stmt: Statement, table: str, part: list[Token], *, path_suffix: str
) -> dict[str, Any] | None:
    """Parse 'CONSTRAINT name KIND ...' or bare 'KIND ...' table constraint.

    Named constraints yield a named_constraint fact (FK payloads included);
    unnamed table-level FOREIGN KEY yields a foreign_key fact.
    """
    if not part or part[0].kind != "word":
        return None
    i = 0
    name = None
    if _kw(part[0]) == "constraint":
        if len(part) < 3 or part[1].kind not in ("word", "qident"):
            return None
        name = part[1].text
        i = 2
    kind = _kw(part[i]) if i < len(part) else ""
    if kind not in ("primary", "unique", "check", "foreign"):
        return None
    payload: dict[str, Any] = {
        "table": table,
        "constraint_name": name,
        "kind": {"primary": "primary_key", "unique": "unique", "check": "check",
                 "foreign": "foreign_key"}[kind],
        "statement_index": stmt.index,
    }
    if kind == "foreign":
        fk = _parse_fk(part[i:])
        if fk:
            payload.update({
                "columns": fk.get("columns"),
                "references_table": fk.get("references_table"),
                "references_columns": fk.get("references_columns"),
                "on_delete": fk.get("on_delete"),
                "on_update": fk.get("on_update"),
            })
    else:
        j = i + 1
        if kind == "primary" and j < len(part) and _kw(part[j]) == "key":
            j += 1
        body = _paren_body(part[j:])
        if body:
            if kind == "check":
                payload["expr"] = _cap(_render(body[0]))
            else:
                payload["columns"] = [_render(p) for p in _split_top_level(body[0])]
    return {
        "fact_kind": "named_constraint" if name else "foreign_key"
        if kind == "foreign" else "named_constraint",
        "payload": payload,
        "anchor": {
            "node_path": _path(stmt, "table", table.split(".")[-1], path_suffix),
            "span": _loc(part),
        },
        **({"relation_mapping": {"status": "unmapped"}} if kind == "foreign" else {}),
    }


# ---------------------------------------------------------------------------
# Grammar: statement handlers


def _unsupported_observation(
    stmt: Statement, construct: str, detail: str, *, path: str | None = None
) -> dict[str, Any]:
    """Explicit unsupported observation; detail names the head only.

    detail is truncated to the statement's leading tokens deliberately:
    echoing construct internals into the payload would smuggle unparsed
    constructs into observation data — the source span carries the full
    location instead.
    """
    return {
        "fact_kind": "operation",
        "payload": {"action": construct, "statement_index": stmt.index,
                    "detail": _cap(detail)},
        "anchor": {
            "node_path": path or _path(stmt, construct.lower().replace(" ", "_")[:40]),
            "span": _loc(stmt.tokens),
        },
        "parse_status": "unsupported",
        "diagnostics": [{
            "code": "unsupported_syntax",
            "message": (
                f"{construct} is outside capability profile {GRAMMAR_REVISION} "
                "(procedural bodies, dynamic SQL and extension syntax are "
                "explicitly unsupported); no structural facts are claimed for "
                "its internals"
            ),
            "anchor": {"node_path": path or _path(stmt, "head"),
                       "span": _loc(stmt.tokens)},
        }],
    }


def _table_name_and_skip(
    stmt: Statement, toks: list[Token], i: int, default_path: str
) -> tuple[str, int, list[dict[str, Any]]]:
    """Parse 'name' skipping IF [NOT] EXISTS / ONLY; returns (name, next_i, diags)."""
    diags: list[dict[str, Any]] = []
    while i < len(toks) and _kw(toks[i]) in ("if", "not", "exists", "only"):
        i += 2 if _kw(toks[i]) == "if" else 1
    name, ni = _qualified_name(toks, i)
    if not name:
        diags.append({
            "code": "missing_anchor",
            "message": "could not parse object name",
            "anchor": {"node_path": _path(stmt, default_path), "span": _loc(toks)},
        })
    return name, ni, diags


def _parse_create_table(stmt: Statement, toks: list[Token]) -> list[dict[str, Any]]:
    name, i, diags = _table_name_and_skip(stmt, toks, 1, "create_table")
    if not name:
        return [_unsupported_observation(stmt, "create table", _render(toks[:3]))]
    body = _paren_body(toks[i:])
    columns: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    fks: list[dict[str, Any]] = []
    parse_status = "complete"
    diagnostics: list[dict[str, Any]] = list(diags)

    if body:
        inner, after = body
        parts = _split_top_level(inner)
        fk_index = 0
        cons_index = 0
        for p_idx, part in enumerate(parts):
            if not part:
                continue
            first = _kw(part[0]) if part[0].kind == "word" else None
            if first in ("constraint", "primary", "unique", "check", "foreign"):
                c = _constraint_observation(
                    stmt, name, part,
                    path_suffix=f"constraints[{cons_index}]",
                )
                if not c:
                    continue
                if c["fact_kind"] == "foreign_key":
                    c["anchor"]["node_path"] = _path(
                        stmt, "table", name.split(".")[-1], f"fks[{fk_index}]"
                    )
                    fks.append(c)
                    fk_index += 1
                else:
                    constraints.append(c)
                    cons_index += 1
            else:
                col, fk = _column_with_fk(
                    stmt, name, part, path_suffix=f"columns[{p_idx}]",
                    fk_index=fk_index,
                )
                if col:
                    columns.append(col)
                if fk:
                    fks.append(fk)
                    fk_index += 1
        rest = toks[after:]
        if rest:
            parse_status = "partial"
            diagnostics.append({
                "code": "unsupported_syntax",
                "message": (
                    f"trailing CREATE TABLE options not in grammar: "
                    f"{_cap(_render(rest[:6]))}; table/column facts are "
                    "complete for the paren body only"
                ),
                "anchor": {"node_path": _path(stmt, "create_table", name),
                           "span": _loc(rest)},
            })
    else:
        parse_status = "partial"
        diagnostics.append({
            "code": "unsupported_syntax",
            "message": "CREATE TABLE without a parseable column body",
            "anchor": {"node_path": _path(stmt, "create_table", name),
                       "span": _loc(toks)},
        })

    table_obs: dict[str, Any] = {
        "fact_kind": "table",
        "payload": {"table": name, "statement_index": stmt.index,
                    "column_count": len(columns)},
        "anchor": {"node_path": _path(stmt, "create_table", name),
                   "span": _loc(stmt.tokens)},
    }
    if parse_status != "complete":
        table_obs["parse_status"] = parse_status
        table_obs["diagnostics"] = diagnostics
    return [table_obs, *columns, *constraints, *fks]


def _parse_alter_table(stmt: Statement, toks: list[Token]) -> list[dict[str, Any]]:
    name, i, _ = _table_name_and_skip(stmt, toks, 2, "alter_table")
    if not name:
        return [_unsupported_observation(stmt, "alter table", _render(toks[:2]))]
    actions = toks[i:]
    if not actions:
        return [_unsupported_observation(stmt, "alter table", _render(toks[:2]))]
    out: list[dict[str, Any]] = []
    j = 0
    counter = 0
    while j < len(actions):
        verb = _kw(actions[j])
        if verb == "add":
            k = j + 1
            if k < len(actions) and _kw(actions[k]) == "column":
                k += 1
            if k < len(actions) and _kw(actions[k]) == "constraint":
                c = _constraint_observation(
                    stmt, name, actions[k:],
                    path_suffix=f"constraints[{counter}]",
                )
                if c:
                    out.append(c)
                    counter += 1
                break
            if k < len(actions) and _kw(actions[k]) in ("primary", "unique", "check", "foreign"):
                c = _constraint_observation(
                    stmt, name, actions[k:],
                    path_suffix=f"constraints[{counter}]",
                )
                if c:
                    out.append(c)
                    counter += 1
                break
            l = k
            while l < len(actions) and actions[l].text != ",":
                l += 1
            col, fk = _column_with_fk(
                stmt, name, actions[k:l], path_suffix=f"columns[{counter}]",
                fk_index=counter,
            )
            if col:
                out.append(col)
            if fk:
                out.append(fk)
            counter += 1
            j = l + 1 if l < len(actions) and actions[l].text == "," else l
        elif verb == "drop":
            k = j + 1
            if k < len(actions) and _kw(actions[k]) == "column":
                k += 1
            col_name, _ = _qualified_name(actions, k)
            out.append({
                "fact_kind": "operation",
                "payload": {"action": "drop_column", "table": name,
                            "column": col_name, "statement_index": stmt.index},
                "anchor": {"node_path": _path(stmt, "alter_table", name),
                           "span": _loc(actions[j:])},
            })
            break
        else:
            out.append({
                "fact_kind": "operation",
                "payload": {"action": "alter_" + (verb or "unknown"), "table": name,
                            "detail": _cap(_render(actions[j:j + 6])),
                            "statement_index": stmt.index},
                "anchor": {"node_path": _path(stmt, "alter_table", name),
                           "span": _loc(actions[j:])},
            })
            break
    if not out:
        out.append(_unsupported_observation(stmt, "alter table", _render(toks)))
    return out


def _parse_create_index(
    stmt: Statement, toks: list[Token], *, unique: bool
) -> list[dict[str, Any]]:
    # toks[0] == INDEX; [CONCURRENTLY] [IF NOT EXISTS] [name] ON table (cols)
    i = 1
    if i < len(toks) and _kw(toks[i]) == "concurrently":
        i += 1
    name = None
    if i < len(toks) and _kw(toks[i]) == "if":
        i += 3  # IF NOT EXISTS
    if i < len(toks) and _kw(toks[i]) != "on":
        name, i = _qualified_name(toks, i)
    if i >= len(toks) or _kw(toks[i]) != "on":
        return [_unsupported_observation(stmt, "create index", _render(toks[:3]))]
    table, ti = _qualified_name(toks, i + 1)
    if not table:
        return [_unsupported_observation(stmt, "create index", _render(toks[:3]))]
    method = None
    if ti < len(toks) and _kw(toks[ti]) == "using":
        method = toks[ti + 1].text.lower()
        ti += 2
    body = _paren_body(toks[ti:])
    if not body:
        return [_unsupported_observation(stmt, "create index", _render(toks[:3]))]
    inner, after = body
    payload = {
        "index_name": name,
        "table": table,
        "columns": [_render(p) for p in _split_top_level(inner)],
        "unique": unique,
        "method": method,
        "statement_index": stmt.index,
    }
    obs: dict[str, Any] = {
        "fact_kind": "index",
        "payload": payload,
        "anchor": {"node_path": _path(stmt, "create_index"),
                   "span": _loc(stmt.tokens)},
    }
    rest = toks[after:]
    if rest:
        payload["where"] = _cap(_render(rest))
        obs["parse_status"] = "partial"
        obs["diagnostics"] = [{
            "code": "unsupported_syntax",
            "message": "partial-index WHERE clause recorded verbatim; semantics not modeled",
            "anchor": {"node_path": _path(stmt, "create_index"), "span": _loc(rest)},
        }]
    return [obs]


def _parse_create_type(stmt: Statement, toks: list[Token]) -> list[dict[str, Any]]:
    # toks[0] == TYPE; name AS ENUM ('a', 'b', ...)
    name, i = _qualified_name(toks, 1)
    if not name:
        return [_unsupported_observation(stmt, "create type", _render(toks[:3]))]
    upper = _upper_seq(toks)
    if "ENUM" in upper and "AS" in upper:
        ei = upper.index("ENUM")
        body = _paren_body(toks[ei + 1:])
        labels: list[str] = []
        if body:
            for p in _split_top_level(body[0]):
                if p and p[0].kind == "string":
                    labels.append(p[0].text)
        return [{
            "fact_kind": "enum_type",
            "payload": {"type_name": name, "labels": labels,
                        "statement_index": stmt.index},
            "anchor": {"node_path": _path(stmt, "create_type", name),
                       "span": _loc(stmt.tokens)},
        }]
    return [_unsupported_observation(
        stmt, "create type", _render(toks[:3]), path=_path(stmt, "create_type", name),
    )]


def _parse_insert(stmt: Statement, toks: list[Token]) -> list[dict[str, Any]]:
    # INSERT INTO name [(cols)] VALUES (...), (...) | SELECT ...
    if len(toks) < 3 or _kw(toks[1]) != "into":
        return [_unsupported_observation(stmt, "insert", _render(toks[:2]))]
    table, i = _qualified_name(toks, 2)
    if not table:
        return [_unsupported_observation(stmt, "insert", _render(toks[:2]))]
    columns: list[str] = []
    if i < len(toks) and toks[i].text == "(":
        body = _paren_body(toks[i:])
        if body:
            columns = [_render(p) for p in _split_top_level(body[0])]
            i = body[1]
    upper = _upper_seq(toks[i:])
    if "VALUES" in upper:
        vi = upper.index("VALUES")
        out: list[dict[str, Any]] = []
        ordinal = 0
        for p in _split_top_level(toks[i + vi + 1:]):
            body = _paren_body(p)
            if not body:
                continue
            values = [_scalar_group(g) for g in _split_top_level(body[0])]
            out.append({
                "fact_kind": "seed_value",
                "payload": {"table": table, "columns": columns, "ordinal": ordinal,
                            "values": values, "statement_index": stmt.index},
                "anchor": {"node_path": _path(stmt, "insert", table.split(".")[-1],
                                              f"values[{ordinal}]"),
                           "span": _loc(body[0])},
            })
            ordinal += 1
        return out
    if "SELECT" in upper:
        si = upper.index("SELECT")
        return [{
            "fact_kind": "operation",
            "payload": {"action": "insert_select", "table": table,
                        "source": _cap(_render(toks[i + si:])),
                        "statement_index": stmt.index},
            "anchor": {"node_path": _path(stmt, "insert", table.split(".")[-1]),
                       "span": _loc(stmt.tokens)},
            "parse_status": "partial",
            "diagnostics": [{
                "code": "unsupported_syntax",
                "message": "INSERT ... SELECT values are not statically derivable; "
                           "no seed facts claimed",
                "anchor": {"node_path": _path(stmt, "insert"),
                           "span": _loc(stmt.tokens)},
            }],
        }]
    return [_unsupported_observation(stmt, "insert", _render(toks[:2]))]


def _parse_statement(stmt: Statement) -> list[dict[str, Any]]:
    toks = stmt.tokens
    if not toks:
        return []
    head = _kw(toks[0])

    if head == "create":
        second = _kw(toks[1]) if len(toks) > 1 else ""
        i = 1
        unique = False
        if second == "unique":
            unique = True
            i = 2
            second = _kw(toks[i]) if i < len(toks) else ""
        if second == "or" and i + 1 < len(toks) and _kw(toks[i + 1]) == "replace":
            i += 2
            second = _kw(toks[i]) if i < len(toks) else ""
        if second == "table":
            return _parse_create_table(stmt, toks[i:])
        if second == "type":
            return _parse_create_type(stmt, toks[i:])
        if second == "index":
            return _parse_create_index(stmt, toks[i:], unique=unique)
        if second in ("function", "procedure", "trigger", "rule", "view",
                      "materialized", "extension", "domain", "sequence", "schema"):
            return [_unsupported_observation(stmt, f"create {second}", _render(toks[:4]))]
        return [_unsupported_observation(stmt, "create", _render(toks[:4]))]

    if head == "alter":
        if len(toks) >= 2 and _kw(toks[1]) == "table":
            return _parse_alter_table(stmt, toks)
        return [_unsupported_observation(stmt, "alter", _render(toks[:2]))]

    if head == "insert":
        return _parse_insert(stmt, toks)

    if head in ("do", "execute", "prepare", "drop"):
        # head-only detail: DO/EXECUTE bodies are exactly the internals that
        # must not leak into observation data
        return [_unsupported_observation(stmt, head, _render(toks[:2]))]

    if head in ("select", "update", "delete", "with", "grant", "revoke",
                "comment", "vacuum", "analyze", "begin", "commit", "lock"):
        return [_unsupported_observation(stmt, head, _render(toks[:4]))]

    return [_unsupported_observation(stmt, "statement", _render(toks[:8]))]


# ---------------------------------------------------------------------------
# Run assembly (replay harness core)


@dataclass
class SqlSource:
    source_uri: str
    revision: str
    text: str
    role: str = "migration"
    language: str = "sql"
    source_kind: str = "sql_migration"


def build_run(sources: list[SqlSource]) -> dict[str, Any]:
    """Parse sources into a full StructureRun dict (pure; no I/O)."""
    read_set = [
        {
            "source_uri": s.source_uri,
            "revision": s.revision,
            "content_hash": hashlib.sha256(s.text.encode("utf-8")).hexdigest(),
            "role": s.role,
        }
        for s in sources
    ]
    rsf = sc.read_set_fingerprint(read_set)
    observations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    for s in sources:
        content_hash = hashlib.sha256(s.text.encode("utf-8")).hexdigest()
        tokens = tokenize(s.text)
        statements = split_statements(tokens)
        for stmt in statements:
            raw_obs = _parse_statement(stmt) or [
                _unsupported_observation(stmt, "empty", "")
            ]
            for k, ro in enumerate(raw_obs):
                node_path = ro["anchor"]["node_path"]
                sfid = sc.source_fact_id_v1(
                    source_uri=s.source_uri,
                    revision=s.revision,
                    content_hash=content_hash,
                    node_path=node_path,
                    fact_kind=ro["fact_kind"],
                )
                payload = dict(ro["payload"])
                payload.setdefault("statement_index", stmt.index)
                oid = sc.observation_id_v1(
                    source_fact_id=sfid,
                    read_set_fingerprint=rsf,
                    payload=payload,
                    parser_identity=PARSER_IDENTITY,
                    parser_revision=PARSER_REVISION,
                    grammar_revision=GRAMMAR_REVISION,
                )
                obs = {
                    "observation_id": oid,
                    "source_fact_id": sfid,
                    "source": {
                        "source_uri": s.source_uri,
                        "revision": s.revision,
                        "content_hash": content_hash,
                        "language": s.language,
                        "source_kind": s.source_kind,
                    },
                    "parser": {
                        "parser_identity": PARSER_IDENTITY,
                        "parser_revision": PARSER_REVISION,
                        "grammar_revision": GRAMMAR_REVISION,
                    },
                    "anchor": {
                        "node_path": node_path,
                        "span": ro["anchor"].get("span"),
                    },
                    "fact_kind": ro["fact_kind"],
                    "payload": payload,
                    "parse_status": ro.get("parse_status", "complete"),
                    "read_set_fingerprint": rsf,
                    "authority_status": sc.AUTHORITY_STATUS,
                }
                if ro.get("diagnostics"):
                    obs["diagnostics"] = ro["diagnostics"]
                if ro.get("relation_mapping") is not None:
                    obs["relation_mapping"] = ro["relation_mapping"]
                errors = sc.validate_structural_observation(obs)
                if errors:
                    findings.append({
                        "finding_id": hashlib.sha256(
                            f"{s.source_uri}:{stmt.index}:{k}:validator".encode()
                        ).hexdigest(),
                        "code": "parser_drift",
                        "message": f"validator rejected observation: {errors}",
                        "anchor": {"node_path": node_path},
                    })
                    continue
                observations.append(obs)

    return {
        "run_id": hashlib.sha256(
            (rsf + PARSER_IDENTITY + PARSER_REVISION + GRAMMAR_REVISION).encode()
        ).hexdigest(),
        "contract_revision": sc.STRUCTURE_CONTRACT_REVISION,
        "parser": {
            "parser_identity": PARSER_IDENTITY,
            "parser_revision": PARSER_REVISION,
            "grammar_revision": GRAMMAR_REVISION,
        },
        "capability_profile": CAPABILITY_PROFILE,
        "read_set": read_set,
        "read_set_fingerprint": rsf,
        "observations": observations,
        "findings": findings,
        "unresolved": [],
        "authority_status": sc.AUTHORITY_STATUS,
    }


def json_canonical(value: Any) -> str:
    return sc._canonical(value)


def snapshot_run(run: dict[str, Any], sources: list[SqlSource]) -> dict[str, Any]:
    """Canonical byte-stable snapshot of a run + its source texts."""
    snap: dict[str, Any] = {
        "snapshot_version": 1,
        "contract_fingerprint": sc.contract_fingerprint(),
        "run": run,
        "sources": [
            {"source_uri": s.source_uri, "revision": s.revision,
             "role": s.role, "language": s.language,
             "source_kind": s.source_kind, "text": s.text}
            for s in sources
        ],
    }
    snap["snapshot_hash"] = hashlib.sha256(
        json_canonical(snap).encode("utf-8")
    ).hexdigest()
    return snap


def load_snapshot(snapshot: dict[str, Any]) -> tuple[dict[str, Any], list[SqlSource]]:
    snap = {k: v for k, v in snapshot.items() if k != "snapshot_hash"}
    sources = [
        SqlSource(source_uri=s["source_uri"], revision=s["revision"],
                  text=s["text"], role=s.get("role", "migration"),
                  language=s.get("language", "sql"),
                  source_kind=s.get("source_kind", "sql_migration"))
        for s in snap["sources"]
    ]
    return snap["run"], sources


def replay(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Byte-stable replay check of a snapshot.

    Rebuilds the run from the snapshot's stored source texts under the pinned
    revisions and compares canonical content and hashes. Replay success means
    the parser is a pure function of its inputs.
    """
    _run, sources = load_snapshot(snapshot)
    rebuilt = snapshot_run(build_run(sources), sources)
    rebuilt_hash = rebuilt.pop("snapshot_hash")
    original_hash = snapshot.get("snapshot_hash")
    original_copy = {k: v for k, v in snapshot.items() if k != "snapshot_hash"}
    obs_ids_orig = sorted(o["observation_id"] for o in snapshot["run"]["observations"])
    obs_ids_new = sorted(o["observation_id"] for o in rebuilt["run"]["observations"])
    return {
        "byte_stable": rebuilt == original_copy and rebuilt_hash == original_hash,
        "snapshot_hash_matches": rebuilt_hash == original_hash,
        "observation_set_matches": obs_ids_orig == obs_ids_new,
        "grammar_revision": GRAMMAR_REVISION,
        "parser_revision": PARSER_REVISION,
        "original_snapshot_hash": original_hash,
        "rebuilt_snapshot_hash": rebuilt_hash,
    }


def classify_drift(snapshot_a: dict[str, Any], snapshot_b: dict[str, Any]) -> dict[str, Any]:
    """Classify the difference between two snapshots.

    Same parser+grammar revisions with differing observation sets is
    parser_drift (a bug — the parser is not a pure function). A grammar
    revision change producing a different population is the expected
    new_population behavior, not drift.
    """
    pa, pb = snapshot_a["run"]["parser"], snapshot_b["run"]["parser"]
    ids_a = sorted(o["observation_id"] for o in snapshot_a["run"]["observations"])
    ids_b = sorted(o["observation_id"] for o in snapshot_b["run"]["observations"])
    same_revisions = pa == pb
    if ids_a == ids_b:
        verdict = "identical"
    elif same_revisions:
        verdict = "parser_drift"
    else:
        verdict = "new_population"
    return {
        "verdict": verdict,
        "same_parser": pa["parser_identity"] == pb["parser_identity"]
        and pa["parser_revision"] == pb["parser_revision"],
        "same_grammar": pa["grammar_revision"] == pb["grammar_revision"],
        "observation_ids_changed": ids_a != ids_b,
        "source_facts_shared": sorted(
            o["source_fact_id"] for o in snapshot_a["run"]["observations"]
        ) == sorted(
            o["source_fact_id"] for o in snapshot_b["run"]["observations"]
        ),
    }
