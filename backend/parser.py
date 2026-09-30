"""Parse and validate the vision model's answer into a trusted intermediate representation (IR).

Nothing here guesses: if the structure is inconsistent, we raise instead of inventing logic.
"""

from __future__ import annotations

import json
import re

from .errors import DiagramInterpretationError, ModelResponseError, UnsupportedDiagramError

UNCLEAR = "Unable to reliably interpret this diagram. Please upload a clearer image."

MAX_NODES = 80
MAX_CLASSES = 40
YES = {"yes", "y", "true", "t"}
NO = {"no", "n", "false", "f"}


# --------------------------------------------------------------------------- helpers
def _clean(value, limit: int = 200) -> str:
    if value is None:
        return ""
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def _ident(value, camel: bool = False) -> str:
    """Make a safe identifier from arbitrary text."""
    s = _clean(value, 80)
    if camel:
        parts = re.split(r"[^A-Za-z0-9]+", s)
        s = "".join(p[:1].upper() + p[1:] for p in parts if p)
    else:
        s = re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_")
    if s and s[0].isdigit():
        s = "_" + s
    return s


def extract_json(raw: str) -> dict:
    if not isinstance(raw, str) or not raw.strip():
        raise ModelResponseError("The vision model returned an empty response. Please try again.")
    text = raw.strip()
    candidates = [text]
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    candidates.append(fenced)
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    raise ModelResponseError(
        "The vision model returned a response that was not valid JSON. "
        "Please try again or upload a clearer image."
    )


# --------------------------------------------------------------------------- entry point
def parse_diagram(raw: str) -> dict:
    data = extract_json(raw)
    dtype = _detect_type(data)
    if dtype == "flowchart":
        ir = _validate_flowchart(data)
    elif dtype == "uml":
        ir = _validate_uml(data)
    else:
        ir = _validate_architecture(data)
    ir["diagram_type"] = dtype
    return ir


def _detect_type(data: dict) -> str:
    declared = _clean(data.get("diagram_type"), 60).lower()
    if "flow" in declared or "algorithm" in declared:
        return "flowchart"
    if "uml" in declared or "class" in declared:
        return "uml"
    if "architect" in declared or "component" in declared or "system" in declared:
        return "architecture"
    if declared in ("", "unknown", "other", "none", "null"):
        if isinstance(data.get("nodes"), list):
            return "flowchart"
        if isinstance(data.get("classes"), list):
            return "uml"
        if isinstance(data.get("components"), list):
            return "architecture"
    if declared and declared != "unsupported":
        raise UnsupportedDiagramError(
            f"'{declared}' diagrams are not supported. Please upload a flowchart, "
            "UML class diagram or simple architecture diagram."
        )
    raise UnsupportedDiagramError(
        "This image does not look like a flowchart, UML class diagram or architecture diagram."
    )


# --------------------------------------------------------------------------- flowchart
_TYPE_ALIASES = {
    "start": "start", "begin": "start", "terminator_start": "start",
    "end": "end", "stop": "end", "finish": "end", "terminator_end": "end",
    "input": "input", "read": "input",
    "output": "output", "print": "output", "display": "output",
    "process": "process", "action": "process", "step": "process", "assignment": "process",
    "statement": "process", "rectangle": "process",
    "decision": "decision", "condition": "decision", "conditional": "decision",
    "if": "decision", "branch": "decision", "diamond": "decision",
}
_INPUT_WORDS = ("input", "read", "enter", "get", "scan", "accept")
_OUTPUT_WORDS = ("print", "display", "output", "show", "write")


def _infer_type(text: str) -> str:
    t = text.strip().lower()
    words = re.findall(r"[a-z]+", t)
    if t.endswith("?"):
        return "decision"
    if words and len(words) <= 2 and words[0] in ("start", "begin"):
        return "start"
    if words and len(words) <= 2 and words[0] in ("end", "stop", "finish", "exit"):
        return "end"
    if words and words[0] in _INPUT_WORDS:
        return "input"
    if words and words[0] in _OUTPUT_WORDS:
        return "output"
    return "process"


def _node_type(raw_type, text: str, has_condition: bool) -> str:
    key = re.sub(r"[^a-z]+", "_", _clean(raw_type, 40).lower()).strip("_")
    alias = _TYPE_ALIASES.get(key)
    inferred = _infer_type(text)
    if has_condition:
        return "decision"
    if alias in (None, "process") and inferred != "process":
        return inferred
    return alias or inferred


def _validate_flowchart(data: dict) -> dict:
    raw_nodes = data.get("nodes")
    raw_edges = data.get("edges", [])
    if not isinstance(raw_nodes, list) or not raw_nodes or not isinstance(raw_edges, list):
        raise DiagramInterpretationError(UNCLEAR)
    if len(raw_nodes) > MAX_NODES:
        raise DiagramInterpretationError("This diagram is too large to interpret reliably.")

    warnings: list[str] = []
    nodes: dict[str, dict] = {}
    for n in raw_nodes:
        if not isinstance(n, dict):
            raise DiagramInterpretationError(UNCLEAR)
        nid = _clean(n.get("id"), 30)
        text = _clean(n.get("text") if n.get("text") is not None else n.get("label"))
        cond = _clean(n.get("condition"))
        if not nid or nid in nodes:
            raise DiagramInterpretationError(UNCLEAR)
        ntype = _node_type(n.get("type"), text, bool(cond))
        if ntype not in ("start", "end") and not (text or cond):
            raise DiagramInterpretationError(UNCLEAR)
        node = {"id": nid, "type": ntype, "text": text or cond}
        if ntype == "decision":
            node["condition"] = (cond or text).rstrip("? ").strip()
            if not node["condition"]:
                raise DiagramInterpretationError(UNCLEAR)
            for key, side in (("yes", "yes"), ("true", "yes"), ("true_branch", "yes"), ("yes_branch", "yes"),
                              ("no", "no"), ("false", "no"), ("false_branch", "no"), ("no_branch", "no")):
                if n.get(key) not in (None, "") and (side + "_hint") not in node:
                    node[side + "_hint"] = _clean(n.get(key), 30)
        nodes[nid] = node

    edges: list[dict] = []
    seen = set()
    for e in raw_edges:
        if not isinstance(e, dict):
            raise DiagramInterpretationError(UNCLEAR)
        src = _clean(e.get("from", e.get("source")), 30)
        dst = _clean(e.get("to", e.get("target")), 30)
        if src not in nodes or dst not in nodes:
            raise DiagramInterpretationError(UNCLEAR)
        label = _clean(e.get("label", e.get("condition")), 30)
        key = (src, dst, label.lower())
        if src == dst or key in seen:
            continue
        seen.add(key)
        edges.append({"from": src, "to": dst, "label": label})

    # decisions may name their branch targets as fields instead of edge labels
    for node in nodes.values():
        if node["type"] != "decision":
            continue
        for side in ("yes", "no"):
            hint = node.pop(side + "_hint", None)
            if hint and hint in nodes:
                labels = YES if side == "yes" else NO
                if not any(e["from"] == node["id"] and e["label"].lower() in labels for e in edges):
                    edges.append({"from": node["id"], "to": hint, "label": side.capitalize()})

    out: dict[str, list[dict]] = {nid: [] for nid in nodes}
    incoming: dict[str, int] = {nid: 0 for nid in nodes}
    for e in edges:
        out[e["from"]].append(e)
        incoming[e["to"]] += 1

    # start node
    starts = [n["id"] for n in nodes.values() if n["type"] == "start"]
    if len(starts) > 1:
        raise DiagramInterpretationError(UNCLEAR)
    if not starts:
        roots = [nid for nid, c in incoming.items() if c == 0]
        if len(roots) != 1:
            raise DiagramInterpretationError(UNCLEAR)
        starts = roots
    start = starts[0]

    # per-node edge rules
    for node in nodes.values():
        nid = node["id"]
        edges_out = out[nid]
        if node["type"] == "end":
            if edges_out:
                warnings.append(f"Arrows leaving the End node '{node['text']}' were ignored.")
                edges = [e for e in edges if e["from"] != nid]
                out[nid] = []
        elif node["type"] == "decision":
            yes = [e for e in edges_out if e["label"].lower() in YES]
            no = [e for e in edges_out if e["label"].lower() in NO]
            if len(yes) != 1 or len(no) != 1 or len(yes) + len(no) != len(edges_out):
                raise DiagramInterpretationError(
                    f"The decision '{node['text']}' does not have clearly labelled Yes and No "
                    "branches. Please upload a clearer image."
                )
        elif len(edges_out) > 1:
            raise DiagramInterpretationError(UNCLEAR)

    # reachability
    reach, stack = {start}, [start]
    while stack:
        cur = stack.pop()
        for e in out[cur]:
            if e["to"] not in reach:
                reach.add(e["to"])
                stack.append(e["to"])
    if len(reach) == 1 and len(nodes) > 1:
        raise DiagramInterpretationError(UNCLEAR)
    dropped = [nid for nid in nodes if nid not in reach]
    if dropped:
        warnings.append(f"{len(dropped)} shape(s) not connected to the Start node were ignored.")
    kept_nodes = [nodes[nid] for nid in nodes if nid in reach]
    kept_edges = [e for e in edges if e["from"] in reach and e["to"] in reach]
    return {"start": start, "nodes": kept_nodes, "edges": kept_edges, "warnings": warnings}


# --------------------------------------------------------------------------- UML
_VIS = {"+": "public", "-": "private", "#": "protected", "~": "package"}
_REL_ALIASES = {
    "inheritance": "inheritance", "extends": "inheritance", "generalization": "inheritance",
    "generalisation": "inheritance", "inherits": "inheritance",
    "implements": "realization", "realization": "realization", "realisation": "realization",
    "association": "association", "aggregation": "aggregation", "composition": "composition",
    "dependency": "dependency", "uses": "dependency",
}


def _clean_type(t) -> str:
    s = _clean(t, 60)
    return s if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_<>\[\], .*]*", s or "x!") else ""


def _parse_attribute(item) -> dict | None:
    if isinstance(item, dict):
        name = _ident(item.get("name"))
        if not name:
            return None
        vis = _clean(item.get("visibility"), 12).lower()
        return {"name": name, "type": _clean_type(item.get("type")),
                "visibility": vis if vis in _VIS.values() else "public"}
    text = _clean(item)
    if not text:
        return None
    vis = "public"
    if text[0] in _VIS:
        vis, text = _VIS[text[0]], text[1:].strip()
    text = text.split("=")[0].strip()
    m = re.fullmatch(r"([A-Za-z_]\w*)\s*:\s*(.+)", text)
    if m:
        name, typ = m.group(1), m.group(2)
    else:
        m = re.fullmatch(r"([A-Za-z_][\w<>\[\],.*]*)\s+([A-Za-z_]\w*)", text)
        if m:
            typ, name = m.group(1), m.group(2)
        else:
            name, typ = _ident(text), ""
    name = _ident(name)
    return {"name": name, "type": _clean_type(typ), "visibility": vis} if name else None


def _parse_params(text: str) -> list[dict]:
    params = []
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*:\s*(.+)", part)
        if m:
            name, typ = m.group(1), m.group(2)
        else:
            m = re.fullmatch(r"([A-Za-z_][\w<>\[\].*]*)\s+([A-Za-z_]\w*)", part)
            name, typ = (m.group(2), m.group(1)) if m else (_ident(part), "")
        name = _ident(name)
        if name:
            params.append({"name": name, "type": _clean_type(typ)})
    return params


def _parse_method(item) -> dict | None:
    if isinstance(item, dict):
        name = _ident(item.get("name"))
        if not name:
            return None
        raw_params = item.get("params") or item.get("parameters") or []
        params = []
        for p in raw_params if isinstance(raw_params, list) else []:
            if isinstance(p, dict) and _ident(p.get("name")):
                params.append({"name": _ident(p.get("name")), "type": _clean_type(p.get("type"))})
            elif isinstance(p, str):
                params.extend(_parse_params(p))
        vis = _clean(item.get("visibility"), 12).lower()
        return {"name": name, "params": params, "return_type": _clean_type(item.get("return_type")) or "void",
                "visibility": vis if vis in _VIS.values() else "public"}
    text = _clean(item)
    if not text:
        return None
    vis = "public"
    if text[0] in _VIS:
        vis, text = _VIS[text[0]], text[1:].strip()
    m = re.fullmatch(r"([A-Za-z_]\w*)\s*\((.*?)\)\s*(?::\s*(.+))?", text)
    if not m:
        m2 = re.fullmatch(r"(?:[A-Za-z_][\w<>\[\].*]*\s+)?([A-Za-z_]\w*)\s*\((.*?)\)", text)
        if not m2:
            return None
        return {"name": m2.group(1), "params": _parse_params(m2.group(2)), "return_type": "void", "visibility": vis}
    return {"name": m.group(1), "params": _parse_params(m.group(2)),
            "return_type": _clean_type(m.group(3)) or "void", "visibility": vis}


def _validate_uml(data: dict) -> dict:
    raw_classes = data.get("classes")
    if not isinstance(raw_classes, list) or not raw_classes:
        raise DiagramInterpretationError(UNCLEAR)
    if len(raw_classes) > MAX_CLASSES:
        raise DiagramInterpretationError("This diagram is too large to interpret reliably.")
    warnings: list[str] = []
    classes: list[dict] = []
    names: set[str] = set()
    for c in raw_classes:
        if not isinstance(c, dict):
            continue
        name = _ident(c.get("name"), camel=True)
        if not name:
            continue
        if name in names:
            warnings.append(f"Duplicate class '{name}' ignored.")
            continue
        names.add(name)
        stereo = _clean(c.get("stereotype") or c.get("kind"), 30).lower().strip("<>« »")
        stereo = stereo if stereo in ("interface", "abstract") else "class"
        attrs = [a for a in (_parse_attribute(x) for x in (c.get("attributes") or [])) if a]
        methods = [m for m in (_parse_method(x) for x in (c.get("methods") or [])) if m]
        classes.append({"name": name, "stereotype": stereo, "attributes": attrs, "methods": methods})
    if not classes:
        raise DiagramInterpretationError(UNCLEAR)

    relationships = []
    for r in data.get("relationships") or data.get("relations") or []:
        if not isinstance(r, dict):
            continue
        src, dst = _ident(r.get("from"), camel=True), _ident(r.get("to"), camel=True)
        if src not in names or dst not in names or src == dst:
            warnings.append("A relationship referring to an unknown class was ignored.")
            continue
        rtype = _REL_ALIASES.get(_clean(r.get("type"), 30).lower(), "association")
        relationships.append({"from": src, "to": dst, "type": rtype, "label": _clean(r.get("label"), 60)})
    return {"classes": classes, "relationships": relationships, "warnings": warnings}


# --------------------------------------------------------------------------- architecture
def _validate_architecture(data: dict) -> dict:
    raw = data.get("components")
    if not isinstance(raw, list) or not raw:
        raise DiagramInterpretationError(UNCLEAR)
    if len(raw) > MAX_CLASSES:
        raise DiagramInterpretationError("This diagram is too large to interpret reliably.")
    warnings: list[str] = []
    components, names = [], set()
    for c in raw:
        if isinstance(c, str):
            c = {"name": c}
        if not isinstance(c, dict):
            continue
        name = _ident(c.get("name"), camel=True)
        if not name or name in names:
            continue
        names.add(name)
        components.append({
            "name": name,
            "label": _clean(c.get("name"), 60),
            "type": _clean(c.get("type"), 30).lower() or "component",
            "description": _clean(c.get("description"), 120),
        })
    if not components:
        raise DiagramInterpretationError(UNCLEAR)
    connections = []
    for c in data.get("connections") or data.get("edges") or []:
        if not isinstance(c, dict):
            continue
        src, dst = _ident(c.get("from", c.get("source")), camel=True), _ident(c.get("to", c.get("target")), camel=True)
        if src not in names or dst not in names or src == dst:
            warnings.append("A connection referring to an unknown component was ignored.")
            continue
        connections.append({"from": src, "to": dst, "label": _clean(c.get("label"), 60)})
    return {"components": components, "connections": connections, "warnings": warnings}


# --------------------------------------------------------------------------- human-readable summary
def summarize_ir(ir: dict) -> list[str]:
    """Readable description of the extracted logic (shown in the 'Extracted Logic' panel)."""
    t = ir.get("diagram_type")
    lines: list[str] = []
    if t == "flowchart":
        nodes = {n["id"]: n for n in ir["nodes"]}
        outs: dict[str, list[dict]] = {}
        for e in ir["edges"]:
            outs.setdefault(e["from"], []).append(e)
        for n in ir["nodes"]:
            kind = n["type"]
            if kind == "decision":
                lines.append(f"Decision: {n['condition']}")
                for e in outs.get(n["id"], []):
                    branch = "True" if e["label"].lower() in YES else "False"
                    lines.append(f"  {branch} → {nodes[e['to']]['text'] or nodes[e['to']]['type']}")
            elif kind == "start":
                lines.append(f"Start: {n['text'] or 'Start'}")
            elif kind == "end":
                lines.append(f"End: {n['text'] or 'End'}")
            else:
                lines.append(f"{kind.capitalize()}: {n['text']}")
    elif t == "uml":
        for c in ir["classes"]:
            tag = "" if c["stereotype"] == "class" else f" <<{c['stereotype']}>>"
            lines.append(f"Class {c['name']}{tag}")
            for a in c["attributes"]:
                lines.append(f"  attribute: {a['name']}" + (f" : {a['type']}" if a["type"] else ""))
            for m in c["methods"]:
                params = ", ".join(p["name"] + (f": {p['type']}" if p["type"] else "") for p in m["params"])
                lines.append(f"  method: {m['name']}({params}) : {m['return_type']}")
        for r in ir["relationships"]:
            lines.append(f"Relationship: {r['from']} --{r['type']}--> {r['to']}")
    elif t == "architecture":
        for c in ir["components"]:
            lines.append(f"Component: {c['name']} ({c['type']})" + (f" - {c['description']}" if c["description"] else ""))
        for c in ir["connections"]:
            lines.append(f"Connection: {c['from']} → {c['to']}" + (f" ({c['label']})" if c["label"] else ""))
    for w in ir.get("warnings", []):
        lines.append(f"Note: {w}")
    return lines
