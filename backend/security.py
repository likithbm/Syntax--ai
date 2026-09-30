"""Heuristic security analysis: deterministic, pattern-based, no AI.

This is NOT a professional SAST tool. It flags a handful of well-known risky patterns so a
human can review them; it will miss things and can produce false positives.
"""

from __future__ import annotations

import re

LABEL = "Heuristic security analysis (pattern-based; not a professional SAST tool)"

_COMMENT_PREFIX = {"python": ("#",), "c": ("//", "/*", "*"), "cpp": ("//", "/*", "*"),
                   "java": ("//", "/*", "*"), "javascript": ("//", "/*", "*")}

# (rule id, severity, finding, recommendation, regex, languages or None for all)
_RULES: list[tuple[str, str, str, str, str, tuple[str, ...] | None]] = [
    ("eval", "high", "Use of eval() executes arbitrary code",
     "Avoid eval(); parse the data explicitly (e.g. int(), json.loads(), ast.literal_eval()).",
     r"\beval\s*\(", ("python", "javascript")),
    ("exec", "high", "Use of exec() executes arbitrary code",
     "Avoid exec(); call the specific function you need instead.", r"\bexec\s*\(", ("python",)),
    ("os-system", "high", "os.system() runs a shell command",
     "Use subprocess with a list of arguments and shell=False, and validate any input.",
     r"\bos\.system\s*\(", ("python",)),
    ("os-popen", "medium", "os.popen() runs a shell command",
     "Use subprocess.run([...], shell=False).", r"\bos\.popen\s*\(", ("python",)),
    ("subprocess-shell", "high", "subprocess call with shell=True",
     "Pass an argument list and leave shell=False to avoid command injection.",
     r"\bsubprocess\.\w+\s*\(.*\bshell\s*=\s*True", ("python",)),
    ("pickle", "medium", "pickle deserialization can execute code",
     "Do not unpickle untrusted data; use JSON.", r"\bpickle\.loads?\s*\(", ("python",)),
    ("c-system", "high", "system() runs a shell command",
     "Avoid system(); call the needed API directly and validate any input.", r"(?<![\w.])system\s*\(", ("c", "cpp")),
    ("c-popen", "high", "popen() runs a shell command", "Avoid popen() with dynamic input.",
     r"(?<![\w.])popen\s*\(", ("c", "cpp")),
    ("c-gets", "high", "gets() cannot limit input length (buffer overflow)",
     "Use fgets() with a buffer size.", r"(?<![\w.])gets\s*\(", ("c", "cpp")),
    ("c-strcpy", "medium", "strcpy()/strcat()/sprintf() do not check buffer bounds",
     "Use strncpy/strncat/snprintf or std::string.", r"(?<![\w.])(strcpy|strcat|sprintf)\s*\(", ("c", "cpp")),
    ("c-scanf-s", "medium", 'scanf("%s") without a width can overflow the buffer',
     'Use a width such as "%99s" or fgets().', r'scanf\s*\(\s*"%s"', ("c", "cpp")),
    ("java-exec", "high", "Runtime.exec() runs an external command",
     "Avoid executing external commands with dynamic input; validate strictly.",
     r"Runtime\s*\.\s*getRuntime\s*\(\s*\)\s*\.\s*exec\s*\(", ("java",)),
    ("java-processbuilder", "medium", "ProcessBuilder starts an external process",
     "Validate all arguments and avoid user-controlled commands.", r"\bnew\s+ProcessBuilder\s*\(", ("java",)),
    ("java-deser", "medium", "ObjectInputStream deserialization of untrusted data is dangerous",
     "Avoid Java serialization for untrusted data.", r"\bObjectInputStream\b", ("java",)),
    ("js-function", "high", "new Function() compiles a string as code",
     "Avoid dynamic code generation.", r"\bnew\s+Function\s*\(", ("javascript",)),
    ("js-child-process", "high", "child_process exec runs a shell command",
     "Use execFile/spawn with an argument array and validate input.",
     r"\b(child_process\s*\.\s*exec(Sync)?|exec(Sync)?)\s*\(", ("javascript",)),
    ("js-innerhtml", "medium", "innerHTML / document.write can enable XSS",
     "Use textContent or sanitise the HTML.", r"(\.innerHTML\s*=|document\.write\s*\()", ("javascript",)),
    ("secret-password", "high", "Hard-coded password",
     "Load secrets from environment variables or a secret manager.",
     r"""(?i)\b(pass(word|wd)?|pwd)\b\s*(=|:|:=)\s*["'][^"'\s][^"']{2,}["']""", None),
    ("secret-apikey", "high", "Hard-coded API key or token",
     "Load keys from environment variables or a secret manager; rotate any exposed key.",
     r"""(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|secret[_-]?key|client[_-]?secret)\b\s*(=|:|:=)\s*["'][^"']{8,}["']""", None),
    ("secret-format", "high", "String that looks like a real credential",
     "Remove it from the source and rotate the credential.",
     r"(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|AIza[0-9A-Za-z_\-]{30,})", None),
    ("secret-private-key", "high", "Embedded private key",
     "Never commit private keys.", r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", None),
    ("sql-concat", "high", "SQL statement built by string concatenation (possible SQL injection)",
     "Use parameterised queries / prepared statements.",
     r"""(?i)["'`]\s*(select|insert|update|delete)\b[^"'`]*["'`]\s*\+""", None),
    ("sql-fstring", "high", "SQL statement built with string interpolation (possible SQL injection)",
     "Use parameterised queries / prepared statements.",
     r"""(?i)\bf["'][^"']*\b(select|insert|update|delete)\b[^"']*\{""", ("python",)),
    ("sql-format", "high", "SQL statement built with string formatting (possible SQL injection)",
     "Use parameterised queries / prepared statements.",
     r"""(?i)["'][^"']*\b(select|insert|update|delete)\b[^"']*["']\s*(%\s*[\w(]|\.format\s*\()""", None),
    ("sql-template", "high", "SQL statement built with a template literal (possible SQL injection)",
     "Use parameterised queries / prepared statements.",
     r"(?i)`[^`]*\b(select|insert|update|delete)\b[^`]*\$\{", ("javascript",)),
]
_COMPILED = [(rid, sev, msg, rec, re.compile(rx), langs) for rid, sev, msg, rec, rx, langs in _RULES]
_SECRET_RULES = {"secret-password", "secret-apikey", "secret-format", "secret-private-key"}
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def scan_code(code: str, language: str) -> list[dict]:
    """Return findings: [{severity, finding, line, recommendation, rule}]"""
    findings: list[dict] = []
    prefixes = _COMMENT_PREFIX.get(language, ("#", "//"))
    for lineno, line in enumerate(code.splitlines(), start=1):
        stripped = line.strip()
        is_comment = stripped.startswith(prefixes)
        for rid, sev, msg, rec, rx, langs in _COMPILED:
            if langs is not None and language not in langs:
                continue
            if is_comment and rid not in _SECRET_RULES:
                continue
            if rx.search(line):
                findings.append({"severity": sev, "finding": msg, "line": lineno,
                                 "recommendation": rec, "rule": rid})
    findings.sort(key=lambda f: (f["line"], _SEVERITY_ORDER[f["severity"]]))
    return findings


def analyse(code: str, language: str) -> dict:
    findings = scan_code(code, language)
    summary = (f"{len(findings)} potential issue(s) found by heuristic patterns."
               if findings else "No issues detected by heuristic patterns.")
    return {"label": LABEL, "summary": summary, "findings": findings}
