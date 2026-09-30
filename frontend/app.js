"use strict";

/* Syntax AI front end: plain JavaScript, no framework.
   Flow: choose image -> POST /api/generate -> poll /api/jobs/{id} -> render result. */

const $ = (id) => document.getElementById(id);
const els = {
  aiStatus: $("ai-status"), aiText: $("ai-status-text"),
  dropzone: $("dropzone"), dzEmpty: $("dz-empty"), preview: $("preview"), fileInput: $("file-input"),
  fileName: $("file-name"), btnUpload: $("btn-upload"), requirement: $("requirement"), language: $("language"),
  btnGenerate: $("btn-generate"), btnClear: $("btn-clear"),
  progress: $("progress"), progressMsg: $("progress-msg"), progressTime: $("progress-time"), steps: $("progress-steps"),
  error: $("error"), emptyResults: $("empty-results"), results: $("results"),
  diagramType: $("diagram-type"), logic: $("logic"), logicJson: $("logic-json"), warnings: $("warnings"),
  codeLang: $("code-lang"), codeFile: $("code-file"), code: $("code").querySelector("code"),
  btnCopy: $("btn-copy"), btnDownload: $("btn-download"),
  secBody: $("sec-body"), secLabel: $("sec-label"), verBody: $("ver-body"),
  historyList: $("history-list"), btnClearHistory: $("btn-clear-history"),
};

const STAGES = [
  ["validating", "Validate image"],
  ["checking_ai", "Check Ollama and model"],
  ["vision", "Read the diagram (vision model)"],
  ["parsing", "Validate extracted logic"],
  ["generating", "Generate code"],
  ["security", "Heuristic security analysis"],
  ["verifying", "Verify generated code"],
  ["saving", "Save to history"],
];

const state = { file: null, previewUrl: null, running: false, current: null, timer: null, poll: null, activeHistory: null };

/* ---------------------------------------------------------------- helpers */
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
async function api(path, options) {
  let resp;
  try {
    resp = await fetch(path, options);
  } catch (e) {
    throw new Error("Cannot reach the Syntax AI server. Is it still running?");
  }
  let data = null;
  try { data = await resp.json(); } catch (e) { /* non-JSON */ }
  if (!resp.ok) {
    const msg = data && data.error && data.error.message ? data.error.message
      : (data && data.detail ? "The request was not valid." : `Request failed (HTTP ${resp.status}).`);
    throw new Error(msg);
  }
  return data;
}
function showError(msg) { els.error.textContent = msg; els.error.hidden = !msg; }

/* ---------------------------------------------------------------- AI status */
async function refreshStatus() {
  try {
    const st = await api("/api/ai-status");
    els.aiStatus.className = "status " + (st.state === "ready" ? "status-ready" : st.state === "model_missing" ? "status-missing" : "status-offline");
    els.aiText.textContent = st.label;
    els.aiStatus.title = st.error || `Ollama at ${st.base_url}`;
    return st;
  } catch (e) {
    els.aiStatus.className = "status status-offline";
    els.aiText.textContent = "Server unreachable";
    els.aiStatus.title = e.message;
    return null;
  }
}

/* ---------------------------------------------------------------- file selection */
function setFile(file) {
  showError("");
  if (!file) return;
  if (!/^image\/(png|jpe?g|webp)$/.test(file.type) && !/\.(png|jpe?g|webp)$/i.test(file.name)) {
    showError("Unsupported file type. Please choose a PNG, JPG or WEBP image.");
    return;
  }
  state.file = file;
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = URL.createObjectURL(file);
  els.preview.src = state.previewUrl;
  els.preview.hidden = false;
  els.dzEmpty.hidden = true;
  els.fileName.textContent = file.name;
  updateGenerate();
}
function updateGenerate() { els.btnGenerate.disabled = !state.file || state.running; }

els.btnUpload.addEventListener("click", () => els.fileInput.click());
els.dropzone.addEventListener("click", () => els.fileInput.click());
els.dropzone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); els.fileInput.click(); } });
els.fileInput.addEventListener("change", () => { setFile(els.fileInput.files[0]); els.fileInput.value = ""; });
["dragenter", "dragover"].forEach((t) => els.dropzone.addEventListener(t, (e) => { e.preventDefault(); els.dropzone.classList.add("drag"); }));
["dragleave", "drop"].forEach((t) => els.dropzone.addEventListener(t, (e) => { e.preventDefault(); els.dropzone.classList.remove("drag"); }));
els.dropzone.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));

document.querySelectorAll("[data-sample]").forEach((btn) => btn.addEventListener("click", async () => {
  try {
    const name = btn.dataset.sample;
    const resp = await fetch("/samples/" + name);
    if (!resp.ok) throw new Error();
    const blob = await resp.blob();
    setFile(new File([blob], name, { type: blob.type || "image/png" }));
  } catch (e) { showError("Could not load the sample image."); }
}));

/* ---------------------------------------------------------------- progress UI */
function buildSteps() {
  els.steps.replaceChildren(...STAGES.map(([key, label]) => { const li = el("li", "", label); li.dataset.stage = key; return li; }));
}
function markStage(stage) {
  const idx = STAGES.findIndex(([k]) => k === stage);
  els.steps.querySelectorAll("li").forEach((li, i) => {
    li.className = idx < 0 ? "" : i < idx ? "done" : i === idx ? "current" : "";
  });
}
function startProgress() {
  buildSteps();
  els.progress.hidden = false;
  els.progress.scrollIntoView({ behavior: "smooth", block: "nearest" });
  els.progressMsg.textContent = "Starting…";
  els.progressTime.textContent = "0s";
  const t0 = Date.now();
  state.timer = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    els.progressTime.textContent = s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
  }, 500);
}
function stopProgress() {
  clearInterval(state.timer); clearTimeout(state.poll);
  els.progress.hidden = true;
}

/* ---------------------------------------------------------------- generate */
async function generate() {
  if (!state.file || state.running) return;
  showError("");
  state.running = true; updateGenerate();
  startProgress();
  try {
    const form = new FormData();
    form.append("image", state.file, state.file.name);
    form.append("language", els.language.value);
    form.append("requirement", els.requirement.value);
    const { job_id } = await api("/api/generate", { method: "POST", body: form });
    const result = await pollJob(job_id);
    renderResult(result);
    state.activeHistory = result.id;
    loadHistory();
  } catch (e) {
    showError(e.message);
  } finally {
    state.running = false; stopProgress(); updateGenerate(); refreshStatus();
  }
}
function pollJob(jobId) {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const job = await api("/api/jobs/" + jobId);
        els.progressMsg.textContent = job.message || "Working…";
        markStage(job.stage);
        if (job.status === "done") return resolve(job.result);
        if (job.status === "error") return reject(new Error(job.error ? job.error.message : "The job failed."));
        state.poll = setTimeout(tick, 700);
      } catch (e) { reject(e); }
    };
    tick();
  });
}
els.btnGenerate.addEventListener("click", generate);

/* ---------------------------------------------------------------- rendering */
function renderResult(r) {
  state.current = r;
  els.emptyResults.hidden = true;
  els.results.hidden = false;

  els.diagramType.textContent = r.diagram_type;
  const logic = r.extracted_logic || {};
  els.logic.textContent = (logic.summary || []).join("\n") || "(nothing extracted)";
  els.logicJson.textContent = JSON.stringify(logic.ir || {}, null, 2);
  els.warnings.replaceChildren(...(r.warnings || []).map((w) => el("li", "", w)));

  els.codeLang.textContent = r.language_label || r.language;
  els.codeFile.textContent = r.code_filename || "";
  els.code.innerHTML = highlight(r.code || "", r.language);

  renderSecurity(r.security || { findings: [] });
  renderVerification(r.verification || {});
  els.results.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderSecurity(sec) {
  els.secLabel.textContent = sec.label || "Heuristic security analysis (pattern-based; not a professional SAST tool)";
  els.secBody.replaceChildren();
  const findings = sec.findings || [];
  if (!findings.length) {
    els.secBody.append(el("p", "ok-note", "No issues detected by heuristic patterns."));
    return;
  }
  const table = el("table");
  table.innerHTML = "<thead><tr><th>Severity</th><th>Finding</th><th>Line</th><th>Recommendation</th></tr></thead>";
  const body = el("tbody");
  findings.forEach((f) => {
    const tr = el("tr");
    const sev = el("td"); sev.append(el("span", "sev sev-" + f.severity, f.severity));
    tr.append(sev, el("td", "", f.finding), el("td", "mono", String(f.line)), el("td", "", f.recommendation));
    body.append(tr);
  });
  table.append(body);
  els.secBody.append(table);
}

function renderVerification(v) {
  els.verBody.replaceChildren();
  const status = v.status || "UNVERIFIED";
  els.verBody.append(el("span", "badge badge-" + status, status.replace("_", " ")));
  els.verBody.append(el("p", "ver-msg", v.message || ""));
  if (v.checks && v.checks.length) {
    const ul = el("ul", "checks");
    v.checks.forEach((c) => {
      const li = el("li");
      li.append(el("span", c.ok ? "pass" : "fail", c.ok ? "✓" : "✗"), el("span", "", c.name));
      if (c.detail) li.append(el("span", "detail", " — " + c.detail));
      ul.append(li);
    });
    els.verBody.append(ul);
  }
}

/* ---------------------------------------------------------------- syntax highlighting */
const KEYWORDS = {
  python: "and as assert break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield True False None self print input int float str".split(" "),
  c: "auto break case char const continue default do double else enum extern float for goto if int long register return short signed sizeof static struct switch typedef union unsigned void volatile while bool true false NULL".split(" "),
  cpp: "auto bool break case catch char class const continue default delete do double else enum explicit false float for friend if inline int long namespace new nullptr operator private protected public return short signed sizeof static struct switch template this throw true try typedef typename union unsigned using virtual void volatile while std string".split(" "),
  java: "abstract boolean break byte case catch char class continue default do double else enum extends final finally float for if implements import instanceof int interface long new null package private protected public return short static super switch this throw throws true false try void while String Scanner System Object".split(" "),
  javascript: "break case catch class const continue default delete do else export extends false finally for function if import in instanceof let new null return super switch this throw true try typeof var void while yield require module".split(" "),
};
function highlight(code, lang) {
  const kw = new Set(KEYWORDS[lang] || []);
  const comment = lang === "python" ? "#[^\\n]*" : "\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/";
  const pre = lang === "c" || lang === "cpp" ? "|(#\\s*include[^\\n]*)" : "|()";
  const re = new RegExp(`(${comment})|("(?:\\\\.|[^"\\\\\\n])*"|'(?:\\\\.|[^'\\\\\\n])*'|\`[^\`]*\`)|(\\b\\d+(?:\\.\\d+)?\\b)|([A-Za-z_]\\w*)${pre}`, "g");
  let out = "", last = 0, m;
  while ((m = re.exec(code)) !== null) {
    out += esc(code.slice(last, m.index));
    const [tok, com, str, num, id, inc] = m;
    if (com) out += `<span class="tok-c">${esc(com)}</span>`;
    else if (str) out += `<span class="tok-s">${esc(str)}</span>`;
    else if (num) out += `<span class="tok-n">${esc(num)}</span>`;
    else if (inc) out += `<span class="tok-p">${esc(inc)}</span>`;
    else if (id && kw.has(id)) out += `<span class="tok-k">${esc(id)}</span>`;
    else out += esc(tok);
    last = m.index + tok.length;
    if (tok.length === 0) re.lastIndex++;
  }
  return out + esc(code.slice(last));
}

/* ---------------------------------------------------------------- copy / download / clear */
els.btnCopy.addEventListener("click", async () => {
  if (!state.current) return;
  const text = state.current.code;
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = el("textarea"); ta.value = text; document.body.append(ta); ta.select();
    try { document.execCommand("copy"); } finally { ta.remove(); }
  }
  const old = els.btnCopy.textContent;
  els.btnCopy.textContent = "Copied ✓";
  setTimeout(() => (els.btnCopy.textContent = old), 1400);
});
els.btnDownload.addEventListener("click", () => {
  if (!state.current) return;
  const blob = new Blob([state.current.code], { type: "text/plain;charset=utf-8" });
  const a = el("a"); a.href = URL.createObjectURL(blob); a.download = state.current.code_filename || "code.txt";
  document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
});
els.btnClear.addEventListener("click", () => {
  if (state.running) return;
  state.file = null; state.current = null; state.activeHistory = null;
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = null;
  els.preview.hidden = true; els.preview.removeAttribute("src"); els.dzEmpty.hidden = false;
  els.fileName.textContent = ""; els.requirement.value = "";
  els.results.hidden = true; els.emptyResults.hidden = false;
  showError(""); updateGenerate(); loadHistory();
});

/* ---------------------------------------------------------------- history */
async function loadHistory() {
  try {
    const items = await api("/api/history");
    els.historyList.replaceChildren();
    if (!items.length) { els.historyList.append(el("p", "hint", "No saved results yet.")); return; }
    items.forEach((it) => {
      const row = el("div", "h-item" + (it.id === state.activeHistory ? " active" : ""));
      row.tabIndex = 0; row.setAttribute("role", "button");
      const main = el("div", "h-main");
      main.append(el("div", "h-name", it.image_filename), el("div", "h-meta", new Date(it.timestamp).toLocaleString()));
      const del = el("button", "h-del", "×"); del.title = "Delete this entry"; del.setAttribute("aria-label", "Delete history entry");
      del.addEventListener("click", async (e) => { e.stopPropagation(); await api("/api/history/" + it.id, { method: "DELETE" }); loadHistory(); });
      row.append(main, el("span", "tag", `${it.diagram_type} · ${it.language}`),
        el("span", "badge badge-" + it.verification_status, it.verification_status.replace("_", " ")), del);
      const open = async () => {
        try {
          const full = await api("/api/history/" + it.id);
          state.activeHistory = it.id;
          renderResult({
            id: full.id, filename: full.image_filename, diagram_type: full.diagram_type, language: full.language,
            language_label: full.language, extracted_logic: full.extracted_logic, code: full.generated_code,
            code_filename: full.code_filename, security: full.security_findings,
            verification: { status: full.verification_status, message: full.verification_message, checks: [] },
            warnings: [],
          });
          loadHistory();
        } catch (e) { showError(e.message); }
      };
      row.addEventListener("click", open);
      row.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      els.historyList.append(row);
    });
  } catch (e) { /* history is non-critical */ }
}
els.btnClearHistory.addEventListener("click", async () => {
  if (!confirm("Delete all saved history?")) return;
  try { await api("/api/history", { method: "DELETE" }); state.activeHistory = null; loadHistory(); } catch (e) { showError(e.message); }
});

/* ---------------------------------------------------------------- init */
refreshStatus();
setInterval(refreshStatus, 10000);
loadHistory();
updateGenerate();
