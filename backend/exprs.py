"""Safe expression handling.

Every condition / assignment coming from the vision model is parsed into a restricted AST
(numbers, variables, arithmetic, comparisons, and/or/not). Anything else is rejected, so
model output can never smuggle arbitrary code into generated programs.
"""

from __future__ import annotations

import ast
import keyword
import re

from .errors import GenerationError

# --------------------------------------------------------------------------- names
RESERVED = set(keyword.kwlist) | {
    "print", "input", "main", "Main", "Scanner", "System", "String", "Math", "Object",
    "console", "require", "process", "nextToken", "sc", "printf", "scanf", "std", "cin",
    "cout", "endl", "int", "float", "double", "char", "long", "short", "bool", "boolean",
    "void", "auto", "const", "static", "struct", "union", "enum", "typedef", "sizeof",
    "signed", "unsigned", "volatile", "register", "extern", "switch", "case", "default",
    "do", "goto", "class", "public", "private", "protected", "final", "new", "this",
    "super", "interface", "package", "import", "throw", "throws", "try", "catch",
    "finally", "null", "true", "false", "var", "let", "function", "typeof", "delete",
    "void", "with", "yield", "export", "extends", "implements", "instanceof", "native",
    "abstract", "synchronized", "transient", "namespace", "template", "typename",
    "virtual", "operator", "friend", "inline", "explicit", "using", "this", "byte",
    "string", "vector", "NULL", "EOF",
}
RESERVED.discard("string")
RESERVED.discard("vector")


def to_var(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", str(name)).strip("_") or "value"
    if s[0].isdigit():
        s = "v_" + s
    if s in RESERVED:
        s += "_"
    return s


class Names:
    """Tracks declared variables and resolves identifiers (case-insensitive fallback)."""

    def __init__(self):
        self.declared: dict[str, str] = {}   # name -> name (ordered)
        self.undeclared: list[str] = []

    def declare(self, raw: str) -> str:
        name = to_var(raw)
        existing = self.resolve_known(name)
        if existing:
            return existing
        self.declared[name] = name
        return name

    def resolve_known(self, raw: str) -> str | None:
        name = to_var(raw)
        if name in self.declared:
            return name
        matches = [d for d in self.declared if d.lower() == name.lower()]
        return matches[0] if len(matches) == 1 else None

    def resolve(self, raw: str) -> str:
        known = self.resolve_known(raw)
        if known:
            return known
        name = to_var(raw)
        if name not in self.undeclared:
            self.undeclared.append(name)
        return name


# --------------------------------------------------------------------------- parsing
_ALLOWED = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.BinOp, ast.Add, ast.Sub, ast.Mult,
    ast.Div, ast.Mod, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.Compare, ast.Eq,
    ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Name, ast.Load, ast.Constant,
)

_NL_PATTERNS = [
    (r"(\w+)\s+(?:is\s+)?(?:greater|more|larger|bigger)\s+than\s+or\s+equal\s+to\s+(\S+)", r"\1 >= \2"),
    (r"(\w+)\s+(?:is\s+)?(?:greater|more|larger|bigger)\s+than\s+(\S+)", r"\1 > \2"),
    (r"(\w+)\s+(?:is\s+)?(?:less|smaller|lower)\s+than\s+or\s+equal\s+to\s+(\S+)", r"\1 <= \2"),
    (r"(\w+)\s+(?:is\s+)?(?:less|smaller|lower)\s+than\s+(\S+)", r"\1 < \2"),
    (r"(\w+)\s+(?:is\s+)?not\s+equal\s+to\s+(\S+)", r"\1 != \2"),
    (r"(\w+)\s+(?:is\s+)?(?:equal\s+to|equals)\s+(\S+)", r"\1 == \2"),
    (r"(?:is\s+)?(\w+)\s+(?:is\s+)?even", r"\1 % 2 == 0"),
    (r"(?:is\s+)?(\w+)\s+(?:is\s+)?odd", r"\1 % 2 != 0"),
    (r"(?:is\s+)?(\w+)\s+(?:is\s+)?positive", r"\1 > 0"),
    (r"(?:is\s+)?(\w+)\s+(?:is\s+)?negative", r"\1 < 0"),
    (r"(?:is\s+)?(\w+)\s+(?:is\s+)?zero", r"\1 == 0"),
]


def normalize_expr(text: str, condition: bool = True) -> str:
    t = str(text).strip().rstrip("?").strip()
    for a, b in (("≥", ">="), ("≤", "<="), ("≠", "!="), ("×", "*"), ("÷", "/"), ("−", "-"), ("<>", "!=")):
        t = t.replace(a, b)
    if condition:
        for pat, rep in _NL_PATTERNS:
            new = re.sub(rf"^\s*{pat}\s*$", rep, t, flags=re.I)
            if new != t:
                t = new
                break
    t = t.replace("&&", " and ").replace("||", " or ")
    t = re.sub(r"!(?!=)", " not ", t)
    t = re.sub(r"\bmod\b", "%", t, flags=re.I)
    t = re.sub(r"\b(and|or|not)\b", lambda m: m.group(1).lower(), t, flags=re.I)
    t = re.sub(r"\btrue\b", "True", t, flags=re.I)
    t = re.sub(r"\bfalse\b", "False", t, flags=re.I)
    if condition:
        t = re.sub(r"(?<![<>=!])=(?!=)", "==", t)
    return re.sub(r"\s+", " ", t).strip()


class _Rename(ast.NodeTransformer):
    def __init__(self, names: Names):
        self.names = names

    def visit_Name(self, node: ast.Name):
        return ast.copy_location(ast.Name(id=self.names.resolve(node.id), ctx=ast.Load()), node)


def parse_expr(text: str, names: Names, *, condition: bool = True) -> str:
    """Return a canonical, validated Python-syntax expression string."""
    t = normalize_expr(text, condition)
    bad = GenerationError(f"The expression '{str(text)[:80]}' could not be converted into code.")
    if not t or not re.fullmatch(r"[A-Za-z0-9_+\-*/%<>=!().\s]+", t):
        raise bad
    try:
        tree = ast.parse(t, mode="eval")
    except (SyntaxError, ValueError):
        raise bad from None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise bad
        if isinstance(node, ast.Constant) and type(node.value) not in (int, float, bool):
            raise bad
    tree = ast.fix_missing_locations(_Rename(names).visit(tree))
    return ast.unparse(tree.body)


# --------------------------------------------------------------------------- analysis helpers
def names_in(src: str) -> list[str]:
    return [n.id for n in ast.walk(ast.parse(src, mode="eval")) if isinstance(n, ast.Name)]


def analyse(src: str) -> dict:
    """Facts about an expression used for type inference."""
    tree = ast.parse(src, mode="eval")
    div_names: set[str] = set()
    mod_names: set[str] = set()
    has_float = False
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            div_names |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            div_has = True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            mod_names |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            has_float = True
    has_div = any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div) for n in ast.walk(tree))
    return {"names": set(names_in(src)), "div_names": div_names, "mod_names": mod_names,
            "has_div": has_div, "has_float": has_float}


_INVERT = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.LtE: ast.Gt,
           ast.Gt: ast.LtE, ast.GtE: ast.Lt}


def negate(src: str) -> str:
    tree = ast.parse(src, mode="eval").body
    if isinstance(tree, ast.Compare) and len(tree.ops) == 1:
        inv = _INVERT[type(tree.ops[0])]()
        return ast.unparse(ast.Compare(left=tree.left, ops=[inv], comparators=tree.comparators))
    if isinstance(tree, ast.UnaryOp) and isinstance(tree.op, ast.Not):
        return ast.unparse(tree.operand)
    return ast.unparse(ast.UnaryOp(op=ast.Not(), operand=tree))


# --------------------------------------------------------------------------- rendering
_BIN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Mod: "%"}
_CMP = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}


def _r(n, clike: bool) -> tuple[str, int]:
    if isinstance(n, ast.Constant):
        v = n.value
        if isinstance(v, bool):
            return (("true" if v else "false") if clike else str(v)), 9
        return repr(v), 9
    if isinstance(n, ast.Name):
        return n.id, 9
    if isinstance(n, ast.UnaryOp):
        s, p = _r(n.operand, clike)
        if isinstance(n.op, ast.Not):
            if clike:
                return "!" + (f"({s})" if p < 9 else s), 8
            return "not " + (f"({s})" if p < 3 else s), 3
        sign = "-" if isinstance(n.op, ast.USub) else "+"
        return sign + (f"({s})" if p < 8 else s), 8
    if isinstance(n, ast.BinOp):
        op = _BIN[type(n.op)]
        own = 6 if op in "+-" else 7
        ls, lp = _r(n.left, clike)
        rs, rp = _r(n.right, clike)
        if lp < own:
            ls = f"({ls})"
        if rp <= own:
            rs = f"({rs})"
        return f"{ls} {op} {rs}", own
    if isinstance(n, ast.Compare):
        operands = [n.left] + list(n.comparators)
        rendered = [_r(o, clike) for o in operands]
        parts = []
        for i, op in enumerate(n.ops):
            ls, lp = rendered[i]
            rs, rp = rendered[i + 1]
            if lp <= 4:
                ls = f"({ls})"
            if rp <= 4:
                rs = f"({rs})"
            parts.append(f"{ls} {_CMP[type(op)]} {rs}")
        if len(parts) == 1:
            return parts[0], 4
        if clike:
            return " && ".join(parts), 2
        return " ".join(f"{rendered[0][0]}" if i == 0 else f"{_CMP[type(n.ops[i-1])]} {rendered[i][0]}"
                        for i in range(len(rendered))), 4
    if isinstance(n, ast.BoolOp):
        is_and = isinstance(n.op, ast.And)
        own = 2 if is_and else 1
        word = ("&&" if is_and else "||") if clike else ("and" if is_and else "or")
        pieces = []
        for v in n.values:
            s, p = _r(v, clike)
            pieces.append(f"({s})" if p < own else s)
        return f" {word} ".join(pieces), own
    raise GenerationError("Unsupported expression.")


def render_expr(src: str, language: str) -> str:
    tree = ast.parse(src, mode="eval").body
    text = _r(tree, language != "python")[0]
    if language == "javascript":  # strict equality is the idiomatic JS form
        text = re.sub(r"(?<![<>=!])==(?!=)", "===", text)
        text = re.sub(r"!=(?!=)", "!==", text)
    return text
