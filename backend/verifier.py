"""Deterministic verification of generated code (no AI involved).

* Flowcharts: compile (where applicable), run with sample inputs under a timeout, and compare
  the program's output with an independent simulation of the extracted flowchart graph.
* UML / architecture skeletons: compile / syntax-check only.

Status is "VERIFIED" only when every check actually passed.
"""

from __future__ import annotations

import ast
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import Settings, get_settings
from .flow import FlowModel, build_flow_model

BASE_TOKENS = ["4", "7", "45", "10", "3", "28"]
MAX_VECTORS = 8
SIM_STEP_LIMIT = 20000


class _SimSkip(Exception):
    """This input cannot be simulated (division by zero, endless loop, ran out of inputs...)."""


# =========================================================================== simulation
def _mod(a, b, c_style: bool):
    if b == 0:
        raise ZeroDivisionError
    if not c_style:
        return a % b
    if isinstance(a, int) and isinstance(b, int):
        r = abs(a) % abs(b)
        return -r if a < 0 else r
    return math.fmod(a, b)


def _eval(node, env: dict, c_style: bool):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id, 0)
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env, c_style)
        if isinstance(node.op, ast.Not):
            return not v
        return -v if isinstance(node.op, ast.USub) else +v
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, env, c_style), _eval(node.right, env, c_style)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b
        if isinstance(node.op, ast.Mod):
            return _mod(a, b, c_style)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env, c_style)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, env, c_style)
            ok = {ast.Eq: left == right, ast.NotEq: left != right, ast.Lt: left < right,
                  ast.LtE: left <= right, ast.Gt: left > right, ast.GtE: left >= right}[type(op)]
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval(v, env, c_style) for v in node.values)
        return any(_eval(v, env, c_style) for v in node.values)
    raise _SimSkip("unsupported expression")


def _ev(src: str, env: dict, c_style: bool):
    return _eval(ast.parse(src, mode="eval").body, env, c_style)


def _fmt(v) -> str:
    return str(v)


def simulate_flowchart(model: FlowModel, tokens: list[str], language: str) -> str:
    """Execute the flowchart graph directly and return the stdout a correct program would print."""
    c_style = language != "python"
    env = {v: ("" if t == "string" else (0.0 if t == "double" else 0)) for v, t in model.types.items()}
    out: list[str] = []
    pos, steps, nid = 0, 0, model.start
    try:
        while nid is not None:
            steps += 1
            if steps > SIM_STEP_LIMIT:
                raise _SimSkip("endless loop")
            if nid in model.cond:
                nid = model.yes[nid] if _ev(model.cond[nid], env, c_style) else model.no[nid]
                continue
            for op in model.ops.get(nid, []):
                k = op[0]
                if k == "input":
                    if pos >= len(tokens):
                        raise _SimSkip("not enough inputs")
                    tok, pos = tokens[pos], pos + 1
                    t = model.types[op[1]]
                    env[op[1]] = int(tok) if t == "int" else (float(tok) if t == "double" else tok)
                    out.append(f"Enter {op[1]}: ")
                elif k == "print_lit":
                    out.append(op[1] + "\n")
                elif k == "print_var":
                    out.append(_fmt(env[op[1]]) + "\n")
                elif k == "print_expr":
                    out.append(_fmt(_ev(op[1], env, c_style)) + "\n")
                elif k == "assign":
                    _, var, aop, src = op
                    val = _ev(src, env, c_style)
                    cur = env.get(var, 0)
                    if aop == "=":
                        env[var] = val
                    elif aop == "+=":
                        env[var] = cur + val
                    elif aop == "-=":
                        env[var] = cur - val
                    elif aop == "*=":
                        env[var] = cur * val
                    elif aop == "/=":
                        env[var] = cur / val
                    elif aop == "%=":
                        env[var] = _mod(cur, val, c_style)
            if model.nodes[nid]["type"] == "end":
                break
            nid = model.succ.get(nid)
    except (ZeroDivisionError, OverflowError, ValueError, TypeError, RecursionError) as exc:
        raise _SimSkip(type(exc).__name__) from None
    return "".join(out)


def outputs_match(expected: str, actual: str) -> bool:
    a, b = expected.split(), actual.split()
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x == y:
            continue
        try:
            if not math.isclose(float(x), float(y), rel_tol=1e-4, abs_tol=1e-6):
                return False
        except ValueError:
            return False
    return True


def _test_values(model: FlowModel) -> list[str]:
    """Input values to test with: a few typical ones plus boundary values around every numeric
    constant used in the flowchart (c-1, c, c+1), so an off-by-one comparison is caught."""
    consts: list[int] = []
    sources = list(model.cond.values())
    for ops in model.ops.values():
        for op in ops:
            if op[0] == "assign":
                sources.append(op[3])
            elif op[0] == "print_expr":
                sources.append(op[1])
    for src in sources:
        for node in ast.walk(ast.parse(src, mode="eval")):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                    and not isinstance(node.value, bool) and abs(node.value) <= 1000:
                k = int(node.value)
                if k not in consts:
                    consts.append(k)
    values: list[str] = []
    for k in consts:
        for v in (k, k - 1, k + 1):
            if str(v) not in values:
                values.append(str(v))
    for v in BASE_TOKENS:
        if v not in values:
            values.append(v)
    # keep boundary values first, but always include two typical values
    return values[: MAX_VECTORS - 2] + [v for v in BASE_TOKENS[:2] if v not in values[: MAX_VECTORS - 2]][:2] \
        if len(values) > MAX_VECTORS else values


# =========================================================================== tooling
def _clean_env() -> dict:
    keep = ("PATH", "SYSTEMROOT", "SystemDrive", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
            "JAVA_HOME", "LANG", "LC_ALL", "COMSPEC", "PATHEXT", "windir")
    return {k: v for k, v in os.environ.items() if k in keep}


def _run(args: list[str], cwd: str, timeout: float, stdin: str = "") -> tuple[int, str, str]:
    proc = subprocess.run(
        args, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=cwd, timeout=timeout, env=_clean_env(), shell=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _tools(language: str) -> dict | None:
    """Locate the compiler/runtime; None if unavailable."""
    if language == "python":
        return {"python": sys.executable}
    if language == "c":
        gcc = shutil.which("gcc")
        return {"cc": gcc} if gcc else None
    if language == "cpp":
        gpp = shutil.which("g++")
        return {"cxx": gpp} if gpp else None
    if language == "java":
        javac, java = shutil.which("javac"), shutil.which("java")
        return {"javac": javac, "java": java} if javac and java else None
    if language == "javascript":
        node = shutil.which("node")
        return {"node": node} if node else None
    return None


def _missing(language: str) -> str:
    need = {"python": "python", "c": "gcc", "cpp": "g++", "java": "javac/java", "javascript": "node"}[language]
    return f"UNVERIFIED – runtime/compiler unavailable ({need} not found)"


def _short(text: str, limit: int = 300) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + " …"


def _result(status: str, message: str, checks: list[dict]) -> dict:
    return {"status": status, "message": message, "checks": checks}


# =========================================================================== public API
def verify(code: str, language: str, ir: dict, filename: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    tools = _tools(language)
    if tools is None:
        return _result("UNVERIFIED", _missing(language), [])
    try:
        with tempfile.TemporaryDirectory(prefix="syntaxai_") as tmp:
            return _verify_in(tmp, code, language, ir, filename, tools, settings)
    except subprocess.TimeoutExpired:
        return _result("FAILED", "FAILED – a step exceeded the time limit.", [])
    except Exception:
        return _result("UNVERIFIED", "UNVERIFIED – verification could not be completed.", [])


def _verify_in(tmp: str, code: str, language: str, ir: dict, filename: str, tools: dict, s: Settings) -> dict:
    path = Path(tmp) / filename
    path.write_text(code, encoding="utf-8")
    is_flow = ir.get("diagram_type") == "flowchart"
    exe = str(Path(tmp) / ("prog.exe" if os.name == "nt" else "prog"))
    checks: list[dict] = []

    # ---- compile / syntax check -----------------------------------------
    try:
        if language == "python":
            cmd = [tools["python"], "-I", "-m", "py_compile", filename]
        elif language == "c":
            cmd = [tools["cc"], "-Wall", "-o", exe, filename] if is_flow else [tools["cc"], "-fsyntax-only", "-Wall", filename]
        elif language == "cpp":
            cmd = [tools["cxx"], "-Wall", "-o", exe, filename] if is_flow else [tools["cxx"], "-fsyntax-only", "-Wall", filename]
        elif language == "java":
            cmd = [tools["javac"], "-d", tmp, filename]
        else:
            cmd = [tools["node"], "--check", filename]
        rc, _out, err = _run(cmd, tmp, s.compile_timeout)
    except subprocess.TimeoutExpired:
        return _result("FAILED", "FAILED – compilation timed out.", [{"name": "Compile", "ok": False, "detail": "timeout"}])
    if rc != 0:
        checks.append({"name": "Compile / syntax check", "ok": False, "detail": _short(err)})
        return _result("FAILED", "FAILED – the generated code did not compile.", checks)
    checks.append({"name": "Compile / syntax check", "ok": True, "detail": "passed"})

    if not is_flow:
        return _result("SYNTAX_OK", "SYNTAX OK – the skeleton compiled/parsed successfully. "
                       "Behaviour is not checked for UML/architecture diagrams.", checks)

    # ---- run against the flowchart simulation -----------------------------
    model = build_flow_model(ir)
    if language == "python":
        run_cmd = [tools["python"], "-I", filename]
    elif language in ("c", "cpp"):
        run_cmd = [exe]
    elif language == "java":
        run_cmd = [tools["java"], "-cp", tmp, "Main"]
    else:
        run_cmd = [tools["node"], filename]

    pool = _test_values(model)
    vectors = [[pool[(k + i) % len(pool)] for i in range(24)] for k in range(min(len(pool), MAX_VECTORS))]
    if not model.has_input:
        vectors = vectors[:1]
    compared, skipped = 0, 0
    n_inputs = sum(1 for ops in model.ops.values() for o in ops if o[0] == "input")
    for tokens in vectors:
        try:
            expected = simulate_flowchart(model, tokens, language)
        except _SimSkip:
            skipped += 1
            continue
        stdin = "\n".join(tokens) + "\n"
        label = "inputs " + ", ".join(tokens[:n_inputs]) if n_inputs else "no inputs"
        try:
            rc, out, err = _run(run_cmd, tmp, s.run_timeout, stdin)
        except subprocess.TimeoutExpired:
            checks.append({"name": f"Run ({label})", "ok": False, "detail": f"timed out after {s.run_timeout:g}s"})
            return _result("FAILED", "FAILED – the program did not finish within the time limit.", checks)
        if rc != 0:
            checks.append({"name": f"Run ({label})", "ok": False, "detail": _short(err) or f"exit code {rc}"})
            return _result("FAILED", "FAILED – the program crashed while running.", checks)
        if not outputs_match(expected, out):
            checks.append({"name": f"Run ({label})", "ok": False,
                           "detail": f"expected {_short(expected.strip(), 120)!r} but got {_short(out.strip(), 120)!r}"})
            return _result("FAILED", "FAILED – program output differs from the flowchart's behaviour.", checks)
        checks.append({"name": f"Run ({label})", "ok": True, "detail": _short(out.strip(), 120)})
        compared += 1

    if compared == 0:
        return _result("SYNTAX_OK", "SYNTAX OK – compiled, but the flowchart could not be simulated for the "
                       "test inputs, so behaviour was not compared.", checks)
    return _result("VERIFIED", f"VERIFIED – compiled and matched the flowchart's behaviour on {compared} "
                   f"test input set{'s' if compared != 1 else ''}.", checks)
