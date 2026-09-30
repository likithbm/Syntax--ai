"""Flowchart -> program. Everything here is deterministic and local (no AI calls)."""

from __future__ import annotations

import ast
import json
import re

from .errors import GenerationError
from .exprs import Names, analyse, negate, parse_expr, render_expr, to_var
from .parser import NO, YES

MAX_STEPS = 3000

_FILLER = {
    "number", "numbers", "value", "values", "the", "a", "an", "of", "integer", "int", "float",
    "decimal", "real", "string", "text", "two", "three", "first", "second", "third", "another",
    "next", "input", "user", "enter", "from", "keyboard", "by", "numeric", "whole", "variable",
}
_STRING_NAMES = {"name", "text", "word", "sentence", "message", "string", "str", "username", "city"}
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(\+=|-=|\*=|/=|%=|=)(?!=)\s*(.+?)\s*$")
_INPUT_RE = re.compile(r"^\s*(?:input|read|enter|get|scan|accept|take)\b\s*[:\-]?\s*(.*)$", re.I)
_OUTPUT_RE = re.compile(r"^\s*(?:print|display|output|show|write|say)\b\s*[:\-]?\s*(.*)$", re.I)


# =========================================================================== model
class FlowModel:
    """Interpreted flowchart: per-node operations, branch targets and inferred variable types."""

    def __init__(self):
        self.start: str = ""
        self.nodes: dict[str, dict] = {}
        self.ops: dict[str, list[tuple]] = {}
        self.cond: dict[str, str] = {}
        self.yes: dict[str, str] = {}
        self.no: dict[str, str] = {}
        self.succ: dict[str, str | None] = {}
        self.types: dict[str, str] = {}
        self.variables: list[str] = []
        self.undeclared: list[str] = []
        self.warnings: list[str] = []
        self.has_input = False


def _parse_input_vars(text: str) -> list[tuple[str, str | None]]:
    m = _INPUT_RE.match(text)
    body = m.group(1) if m else text
    out = []
    for part in re.split(r"\s*(?:,|;|&|\band\b)\s*", body, flags=re.I):
        words = re.findall(r"[A-Za-z0-9_]+", part)
        if not words:
            continue
        lowered = {w.lower() for w in words}
        hint = None
        if lowered & {"float", "decimal", "real"}:
            hint = "double"
        elif lowered & {"string", "text"}:
            hint = "string"
        elif lowered & {"integer", "int"}:
            hint = "int"
        if len(words) == 1:
            rest = words  # a lone word is always the variable, even if it looks like a filler word ("a")
        else:
            rest = [w for w in words if w.lower() not in _FILLER]
            if not rest and words[-1].lower() not in _FILLER - {"a", "an"}:
                rest = [words[-1]]
        if rest:
            out.append(("_".join(rest[:3]), hint))
    if not out:
        raise GenerationError(f"Could not identify the variable(s) in the input step '{text[:60]}'.")
    return out


def _parse_process(text: str) -> list[tuple]:
    """Syntactic split of a process box: ('assign', lhs, op, rhs_text, hint) or ('comment', text)."""
    t = text.strip()
    for a in ("←", ":=", "<-"):
        t = t.replace(a, "=")
    m = re.match(r"^(increment|decrement)\s+([A-Za-z_]\w*)\s*$", t, re.I)
    if m:
        return [("assign", m.group(2), "+=" if m.group(1).lower() == "increment" else "-=", "1", None)]
    m = re.match(r"^([A-Za-z_]\w*)\s*(\+\+|--)\s*$", t)
    if m:
        return [("assign", m.group(1), "+=" if m.group(2) == "++" else "-=", "1", None)]
    verb = re.match(r"^(initiali[sz]e|set|let|assign|calculate|compute|update|store|declare)\s+", t, re.I)
    if verb:
        t = t[verb.end():]
        if verb.group(1).lower() in ("set", "assign") and "=" not in t:
            t = re.sub(r"\s+to\s+", " = ", t, count=1, flags=re.I)
    hint = None
    tm = re.match(r"^(int|integer|long|float|double|var|const|string)\s+", t, re.I)
    if tm:
        hint = {"float": "double", "double": "double", "string": "string"}.get(tm.group(1).lower(), "int")
        t = t[tm.end():]
    pieces = [p for p in re.split(r"\s*(?:;|,|\band\b)\s*", t, flags=re.I) if p]
    parsed = [_ASSIGN_RE.match(p) for p in pieces]
    if pieces and all(parsed):
        return [("assign", g.group(1), g.group(2), g.group(3), hint) for g in parsed]
    g = _ASSIGN_RE.match(t)
    if g:
        return [("assign", g.group(1), g.group(2), g.group(3), hint)]
    return [("comment", text.strip())]


def build_flow_model(ir: dict) -> FlowModel:
    m = FlowModel()
    m.start = ir["start"]
    m.nodes = {n["id"]: n for n in ir["nodes"]}
    names = Names()
    hints: dict[str, str] = {}

    outs: dict[str, list[dict]] = {nid: [] for nid in m.nodes}
    for e in ir["edges"]:
        outs[e["from"]].append(e)

    # pass 1: declare variables from input steps and assignment targets
    raw_ops: dict[str, list[tuple]] = {}
    for nid, n in m.nodes.items():
        t = n["type"]
        if t == "input":
            items = _parse_input_vars(n["text"])
            raw_ops[nid] = [("input", names.declare(v), h) for v, h in items]
            for _, v, h in raw_ops[nid]:
                if h:
                    hints[v] = h
                elif v.lower() in _STRING_NAMES:
                    hints[v] = "string"
            m.has_input = True
        elif t == "process":
            ops = _parse_process(n["text"])
            fixed = []
            for op in ops:
                if op[0] == "assign":
                    v = names.declare(op[1])
                    if op[4]:
                        hints[v] = op[4]
                    fixed.append(("assign", v, op[2], op[3]))
                else:
                    fixed.append(op)
            raw_ops[nid] = fixed

    # pass 2: validate expressions / interpret outputs and decisions
    for nid, n in m.nodes.items():
        t = n["type"]
        if t == "input":
            m.ops[nid] = [("input", v) for _, v, _h in raw_ops[nid]]
        elif t == "process":
            ops = []
            for op in raw_ops[nid]:
                if op[0] == "assign":
                    try:
                        expr = parse_expr(op[3], names, condition=False)
                        ops.append(("assign", op[1], op[2], expr))
                    except GenerationError:
                        ops.append(("comment", f"{op[1]} {op[2]} {op[3]}"))
                        m.warnings.append(
                            f"Step '{n['text'][:50]}' could not be translated and was left as a comment."
                        )
                else:
                    ops.append(op)
                    m.warnings.append(f"Step '{op[1][:50]}' could not be translated and was left as a comment.")
            m.ops[nid] = ops
        elif t == "output":
            m.ops[nid] = [_interpret_output(n["text"], names)]
        elif t == "decision":
            m.cond[nid] = parse_expr(n["condition"], names, condition=True)
            for e in outs[nid]:
                (m.yes if e["label"].lower() in YES else m.no)[nid] = e["to"]
            if nid not in m.yes or nid not in m.no:
                raise GenerationError(f"Decision '{n['text'][:60]}' needs both a Yes and a No branch.")
        else:
            m.ops[nid] = []
        if t != "decision":
            m.succ[nid] = outs[nid][0]["to"] if outs[nid] else None

    m.variables = list(names.declared) + [u for u in names.undeclared if u not in names.declared]
    m.undeclared = [u for u in names.undeclared if u not in names.declared]
    for u in m.undeclared:
        m.warnings.append(f"Variable '{u}' is used but never read or assigned in the diagram; it starts at 0.")
    m.types = _infer_types(m, hints)
    return m


def _interpret_output(text: str, names: Names) -> tuple:
    mo = _OUTPUT_RE.match(text)
    rest = (mo.group(1) if mo else text).strip()
    if not rest:
        return ("print_lit", text.strip())
    if len(rest) >= 2 and rest[0] in "\"'“‘" and rest[-1] in "\"'”’":
        return ("print_lit", rest[1:-1])
    if re.fullmatch(r"[A-Za-z_]\w*", rest):
        known = names.resolve_known(rest)
        return ("print_var", known) if known else ("print_lit", rest)
    if re.search(r"[+\-*/%]", rest):
        probe = Names()
        probe.declared = dict(names.declared)
        try:
            expr = parse_expr(rest, probe, condition=False)
            if not probe.undeclared:
                return ("print_expr", expr)
        except GenerationError:
            pass
    return ("print_lit", rest)


def _infer_types(m: FlowModel, hints: dict[str, str]) -> dict[str, str]:
    types = {v: hints.get(v, "int") for v in m.variables}
    exprs: list[tuple[str | None, str]] = []  # (assigned var or None, expression)
    for nid, ops in m.ops.items():
        for op in ops:
            if op[0] == "assign":
                src = op[3] if op[2] == "=" else f"{op[1]} {op[2][0]} ({op[3]})"
                exprs.append((op[1], src))
            elif op[0] == "print_expr":
                exprs.append((None, op[1]))
    exprs += [(None, c) for c in m.cond.values()]

    force_int: set[str] = set()
    for _, src in exprs:
        force_int |= analyse(src)["mod_names"]

    def settable(v: str) -> bool:
        return v in types and types[v] != "string" and v not in force_int and v not in hints

    for lhs, src in exprs:
        a = analyse(src)
        if a["has_div"] or a["has_float"]:
            for v in a["div_names"] | (a["names"] if a["has_float"] else set()):
                if settable(v):
                    types[v] = "double"
            if lhs and settable(lhs):
                types[lhs] = "double"
    for _ in range(5):  # propagate: x = (double expr) makes x double
        changed = False
        for lhs, src in exprs:
            if lhs and settable(lhs) and types[lhs] != "double":
                if any(types.get(n) == "double" for n in analyse(src)["names"]):
                    types[lhs] = "double"
                    changed = True
        if not changed:
            break
    return types


# =========================================================================== structuring
class _Structurer:
    def __init__(self, m: FlowModel):
        self.m = m
        self.steps = 0
        self.headers: set[str] = set()
        self._find_headers()
        self.loop_sets = {h: self._loop_set(h) for h in self.headers}

    def succs(self, nid: str) -> list[str]:
        if nid in self.m.cond:
            return [self.m.yes[nid], self.m.no[nid]]
        nxt = self.m.succ.get(nid)
        return [nxt] if nxt else []

    def _find_headers(self):
        color: dict[str, int] = {}
        stack = [(self.m.start, iter(self.succs(self.m.start)))]
        color[self.m.start] = 1
        while stack:
            nid, it = stack[-1]
            for nxt in it:
                if color.get(nxt) == 1:
                    self.headers.add(nxt)
                elif nxt not in color:
                    color[nxt] = 1
                    stack.append((nxt, iter(self.succs(nxt))))
                    break
            else:
                color[nid] = 2
                stack.pop()

    def reach(self, start: str, blocked: frozenset = frozenset()) -> set[str]:
        """Nodes reachable from start. Nodes in `blocked` (active loop headers) are included
        but not expanded, so a loop's back edge is not mistaken for a way to 'merge'."""
        seen, todo = {start}, [start]
        while todo:
            cur = todo.pop()
            if cur in blocked and cur != start:
                continue
            for nxt in self.succs(cur):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return seen

    def _loop_set(self, header: str) -> set[str]:
        fwd = self.reach(header)
        return {n for n in fwd if header in self.reach(n)}

    def merge(self, a: str, b: str, active: list) -> str | None:
        blocked = frozenset(x["header"] for x in active)
        reach_b = self.reach(b, blocked)
        seen, queue = {a}, [a]
        while queue:
            cur = queue.pop(0)
            if cur in reach_b:
                return cur
            if cur in blocked and cur != a:
                continue
            for nxt in self.succs(cur):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return None

    # ------------------------------------------------------------------ walk
    def build(self) -> list[tuple]:
        return _clean_block(self.walk(self.m.start, None, []))

    def walk(self, nid, stop, active, inside_once=None) -> list[tuple]:
        m = self.m
        out: list[tuple] = []
        while nid is not None and nid != stop:
            self.steps += 1
            if self.steps > MAX_STEPS:
                raise GenerationError("The flowchart is too complex to convert reliably.")
            hdr = None if nid == inside_once else next((a for a in active if a["header"] == nid), None)
            if hdr is not None:
                if hdr is not active[-1]:
                    raise GenerationError("Unsupported flowchart: a jump back to an outer loop.")
                out.append(("continue",))
                return out
            node = m.nodes[nid]

            if nid in self.headers and nid != inside_once:
                loop_set = self.loop_sets[nid]
                entry = {"header": nid, "set": loop_set, "exit": None}
                if nid in m.cond:
                    y_in, n_in = m.yes[nid] in loop_set, m.no[nid] in loop_set
                    if y_in != n_in:
                        cond = m.cond[nid] if y_in else negate(m.cond[nid])
                        start_b, exit_t = (m.yes[nid], m.no[nid]) if y_in else (m.no[nid], m.yes[nid])
                        active.append(entry)
                        body = self.walk(start_b, None, active)
                        active.pop()
                        out.append(("while", cond, _clean_block(body)))
                        nid, inside_once = exit_t, None
                        continue
                active.append(entry)
                body = self.walk(nid, None, active, inside_once=nid)
                active.pop()
                out.append(("while_true", _clean_block(body)))
                nid, inside_once = entry["exit"], None
                continue
            inside_once = None

            if nid in m.cond:
                yes, no, cond = m.yes[nid], m.no[nid], m.cond[nid]
                if active:
                    cur = active[-1]
                    y_in, n_in = yes in cur["set"], no in cur["set"]
                    if y_in != n_in:
                        exit_t, keep = (no, yes) if y_in else (yes, no)
                        if cur["exit"] is None:
                            cur["exit"] = exit_t
                        elif cur["exit"] != exit_t:
                            raise GenerationError("Unsupported flowchart: a loop with several exits.")
                        out.append(("if", negate(cond) if y_in else cond, [("break",)], []))
                        nid = keep
                        continue
                merge = self.merge(yes, no, active)
                then_b = self.walk(yes, merge, active)
                else_b = self.walk(no, merge, active)
                out.append(("if", cond, then_b, else_b))
                nid = merge
                continue

            for op in m.ops.get(nid, []):
                out.append(op)
            if node["type"] == "end":
                break
            nid = m.succ.get(nid)
        return out


def _strip_trailing_continue(block: list[tuple]) -> list[tuple]:
    if not block:
        return block
    last = block[-1]
    if last[0] == "continue":
        return block[:-1]
    if last[0] == "if":
        return block[:-1] + [("if", last[1], _strip_trailing_continue(last[2]), _strip_trailing_continue(last[3]))]
    return block


def _clean_block(block: list[tuple]) -> list[tuple]:
    """Normalise if/else shape: no empty 'then', no useless else."""
    out = []
    for s in block:
        if s[0] == "if":
            cond, a, b = s[1], _clean_block(s[2]), _clean_block(s[3])
            if not a and b:
                cond, a, b = negate(cond), b, []
            out.append(("if", cond, a, b))
        elif s[0] == "while":
            out.append(("while", s[1], _clean_block(s[2])))
        elif s[0] == "while_true":
            out.append(("while_true", _clean_block(s[1])))
        else:
            out.append(s)
    return out


def build_program(ir: dict) -> tuple[FlowModel, list[tuple]]:
    model = build_flow_model(ir)
    st = _Structurer(model)
    stmts = st.build()
    # loops: a trailing 'continue' is implicit
    stmts = _finalize_loops(stmts)
    return model, stmts


def _finalize_loops(block: list[tuple]) -> list[tuple]:
    out = []
    for s in block:
        if s[0] == "while":
            out.append(("while", s[1], _strip_trailing_continue(_finalize_loops(s[2]))))
        elif s[0] == "while_true":
            out.append(("while_true", _strip_trailing_continue(_finalize_loops(s[1]))))
        elif s[0] == "if":
            out.append(("if", s[1], _finalize_loops(s[2]), _finalize_loops(s[3])))
        else:
            out.append(s)
    return out


# =========================================================================== rendering
def _comment_text(text: str, limit: int = 100) -> str:
    """Make arbitrary text safe inside a one-line comment (no backslash line-splicing, no comment terminators)."""
    t = re.sub(r"[\x00-\x1f\x7f\\]+", " ", str(text)).replace("*/", "* /").replace("/*", "/ *")
    return re.sub(r"\s+", " ", t).strip()[:limit]


def _str_lit(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def _c_fmt_literal(text: str) -> str:
    """Escape for use inside a printf format string."""
    return _str_lit(text)[1:-1].replace("%", "%%")


def _prompt(var: str) -> str:
    return f"Enter {var}: "


def _is_double_expr(src: str, types: dict[str, str]) -> bool:
    a = analyse(src)
    return a["has_div"] or a["has_float"] or any(types.get(n) == "double" for n in a["names"])


def _header_comments(language: str, requirement: str, what: str) -> list[str]:
    c = "#" if language == "python" else "//"
    lines = [f"{c} Generated by Syntax AI from {what}"]
    req = _comment_text(requirement or "", 200)
    if req:
        lines.append(f"{c} Requirement (recorded, not interpreted): {req}")
    return lines


def render_flowchart(model: FlowModel, stmts: list[tuple], language: str, requirement: str = "") -> str:
    types = model.types
    lines: list[str] = []
    ind = "    "

    def emit(depth: int, text: str):
        lines.append(ind * depth + text)

    e = lambda src: render_expr(src, language)  # noqa: E731

    # ---------------------------------------------------------------- python
    if language == "python":
        lines += _header_comments("python", requirement, "a flowchart")
        lines.append("")
        for u in model.undeclared:
            emit(0, f"{u} = 0  # used in the diagram but never read or assigned")
        if model.undeclared:
            lines.append("")

        def block(b, depth):
            if not b:
                emit(depth, "pass")
                return
            for s in b:
                k = s[0]
                if k == "input":
                    t = types[s[1]]
                    conv = {"int": "int(input({p}))", "double": "float(input({p}))", "string": "input({p})"}[t]
                    emit(depth, f"{s[1]} = " + conv.format(p=_str_lit(_prompt(s[1]))))
                elif k == "print_lit":
                    emit(depth, f"print({_str_lit(s[1])})")
                elif k == "print_var":
                    emit(depth, f"print({s[1]})")
                elif k == "print_expr":
                    emit(depth, f"print({e(s[1])})")
                elif k == "assign":
                    emit(depth, f"{s[1]} {s[2]} {e(s[3])}")
                elif k == "comment":
                    emit(depth, f"# TODO (not translated): {_comment_text(s[1])}")
                elif k == "if":
                    emit(depth, f"if {e(s[1])}:")
                    block(s[2], depth + 1)
                    rest = s[3]
                    while len(rest) == 1 and rest[0][0] == "if":
                        emit(depth, f"elif {e(rest[0][1])}:")
                        block(rest[0][2], depth + 1)
                        rest = rest[0][3]
                    if rest:
                        emit(depth, "else:")
                        block(rest, depth + 1)
                elif k == "while":
                    emit(depth, f"while {e(s[1])}:")
                    block(s[2], depth + 1)
                elif k == "while_true":
                    emit(depth, "while True:")
                    block(s[1], depth + 1)
                elif k == "break":
                    emit(depth, "break")
                elif k == "continue":
                    emit(depth, "continue")

        block(stmts, 0)
        return "\n".join(lines) + "\n"

    # ---------------------------------------------------------------- C-family
    def decl(v: str) -> str:
        t = types[v]
        if language == "javascript":
            return f'let {v} = {chr(34)*2 if t == "string" else 0};'
        if language == "c":
            return f'char {v}[100] = "";' if t == "string" else f"{'double' if t == 'double' else 'int'} {v} = 0;"
        if language == "cpp":
            return f"std::string {v};" if t == "string" else f"{'double' if t == 'double' else 'int'} {v} = 0;"
        return f'String {v} = "";' if t == "string" else f"{'double' if t == 'double' else 'int'} {v} = 0;"

    def input_stmt(v: str, depth: int):
        t = types[v]
        p = _str_lit(_prompt(v))
        if language == "c":
            emit(depth, f"printf({_str_lit(_prompt(v).replace('%', '%%'))});")
            spec = {"int": "%d", "double": "%lf", "string": "%99s"}[t]
            target = v if t == "string" else f"&{v}"
            emit(depth, f'if (scanf("{spec}", {target}) != 1) {{ return 1; }}')
        elif language == "cpp":
            emit(depth, f"std::cout << {p};")
            emit(depth, f"if (!(std::cin >> {v})) {{ return 1; }}")
        elif language == "java":
            emit(depth, f"System.out.print({p});")
            fn = {"int": "nextInt", "double": "nextDouble", "string": "next"}[t]
            emit(depth, f"{v} = sc.{fn}();")
        else:
            emit(depth, f"process.stdout.write({p});")
            conv = {"int": "parseInt(nextToken(), 10)", "double": "parseFloat(nextToken())", "string": "String(nextToken())"}[t]
            emit(depth, f"{v} = {conv};")

    def print_lit(text: str, depth: int):
        if language == "c":
            emit(depth, f'printf("{_c_fmt_literal(text)}\\n");')
        elif language == "cpp":
            emit(depth, f"std::cout << {_str_lit(text)} << std::endl;")
        elif language == "java":
            emit(depth, f"System.out.println({_str_lit(text)});")
        else:
            emit(depth, f"console.log({_str_lit(text)});")

    def print_value(src: str, is_double: bool, is_string: bool, depth: int):
        if language == "c":
            spec = "%s" if is_string else ("%g" if is_double else "%d")
            emit(depth, f'printf("{spec}\\n", {src});')
        elif language == "cpp":
            emit(depth, f"std::cout << {src} << std::endl;")
        elif language == "java":
            emit(depth, f"System.out.println({src});")
        else:
            emit(depth, f"console.log({src});")

    def block(b, depth):
        for s in b:
            k = s[0]
            if k == "input":
                input_stmt(s[1], depth)
            elif k == "print_lit":
                print_lit(s[1], depth)
            elif k == "print_var":
                t = types[s[1]]
                print_value(s[1], t == "double", t == "string", depth)
            elif k == "print_expr":
                print_value(e(s[1]), _is_double_expr(s[1], types), False, depth)
            elif k == "assign":
                emit(depth, f"{s[1]} {s[2]} {e(s[3])};")
            elif k == "comment":
                emit(depth, f"// TODO (not translated): {_comment_text(s[1])}")
            elif k == "if":
                emit(depth, f"if ({e(s[1])}) {{")
                block(s[2], depth + 1)
                rest = s[3]
                while len(rest) == 1 and rest[0][0] == "if":
                    emit(depth, f"}} else if ({e(rest[0][1])}) {{")
                    block(rest[0][2], depth + 1)
                    rest = rest[0][3]
                if rest:
                    emit(depth, "} else {")
                    block(rest, depth + 1)
                emit(depth, "}")
            elif k == "while":
                emit(depth, f"while ({e(s[1])}) {{")
                block(s[2], depth + 1)
                emit(depth, "}")
            elif k == "while_true":
                emit(depth, "while (true) {")
                block(s[1], depth + 1)
                emit(depth, "}")
            elif k == "break":
                emit(depth, "break;")
            elif k == "continue":
                emit(depth, "continue;")

    lines += _header_comments(language, requirement, "a flowchart")
    body_depth = 1
    if language == "c":
        lines += ["#include <stdio.h>", "#include <stdbool.h>", "", "int main(void) {"]
    elif language == "cpp":
        lines += ["#include <iostream>", "#include <string>", "", "int main() {"]
    elif language == "java":
        if model.has_input:
            lines += ["import java.util.Scanner;", ""]
        lines += ["public class Main {", "    public static void main(String[] args) {"]
        body_depth = 2
        if model.has_input:
            emit(body_depth, "Scanner sc = new Scanner(System.in);")
    else:
        lines += ['"use strict";', ""]
        body_depth = 0
        if model.has_input:
            lines += [
                'const _tokens = require("fs").readFileSync(0, "utf8").split(/\\s+/).filter(Boolean);',
                "let _pos = 0;",
                "function nextToken() { return _tokens[_pos++]; }",
                "",
            ]
    for v in model.variables:
        emit(body_depth, decl(v))
    if model.variables:
        lines.append("")
    block(stmts, body_depth)
    if language == "c" or language == "cpp":
        lines += ["", "    return 0;", "}"]
    elif language == "java":
        lines += ["    }", "}"]
    return "\n".join(lines) + "\n"
