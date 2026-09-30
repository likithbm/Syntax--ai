# Syntax AI — Diagram-to-Code Generator

Upload a flowchart, UML class diagram or simple architecture diagram. A **local** vision model
(Qwen2.5-VL 3B through Ollama) reads it, and the code is generated **locally** from what it read.
No OpenAI / Gemini / Claude API, no API keys, no login, no paid services.

```
Uploaded image
  -> ONE Ollama vision request (qwen2.5vl:3b)        <- the only AI call
  -> structured JSON, validated (never trusted blindly)
  -> local code generation (Python / C / C++ / Java / JavaScript)
  -> heuristic security analysis   (deterministic, no AI)
  -> verification: compile + run    (deterministic, no AI)
  -> result + saved to SQLite history
```

## Requirements (Windows)

* Python 3.10 or newer
* [Ollama for Windows](https://ollama.com/download) (keep it up to date; Qwen2.5-VL needs a recent version)
* Optional, for the *Verification* step in other languages: `gcc`/`g++` (MinGW-w64 or MSYS2),
  a JDK (`javac`, `java`) and Node.js — all on `PATH`. Python verification needs nothing extra.
  Anything missing is reported honestly as `UNVERIFIED – runtime/compiler unavailable`.

## Install (PowerShell)

```powershell
cd syntax-ai
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
ollama pull qwen2.5vl:3b
```

If script execution is blocked: `Set-ExecutionPolicy -Scope Process Bypass` and activate again.

Check that the model is installed:

```powershell
ollama list
```

You should see `qwen2.5vl:3b` in the list.

## Run

```powershell
python -m uvicorn backend.app:app --reload --port 8000
```

Open <http://localhost:8000>. The header pill shows **AI Ready · qwen2.5vl:3b** when Ollama is
reachable and the model is installed, otherwise **AI Offline** (or *Model missing*).

## Demo steps

1. Open <http://localhost:8000> and check the pill says **AI Ready · qwen2.5vl:3b**.
2. Click the **Even / Odd** sample chip (or **Upload Diagram** to use your own image).
3. Leave the language on **Python** and click **Generate Code**.
4. Watch the progress card: the vision model runs once (on CPU this may take from several seconds
   to a couple of minutes), then everything else finishes almost instantly.
5. Read the four result panels:
   * **Extracted Logic** – `Input n`, `Decision: n % 2 == 0`, `True → Even`, `False → Odd`
   * **Generated Code** – syntax-highlighted; use **Copy Code** / **Download Code**
   * **Security Analysis** – heuristic scan of the generated code
   * **Verification** – `VERIFIED` only if the program compiled and its output matched the flowchart
6. Switch the language to Java / C / C++ / JavaScript and click **Generate Code** again.
7. Try **Sum loop** (a `while` loop) and **Pass / Fail** to show the code follows the diagram.
8. Scroll to **History** and click an entry to reload it. **Clear** resets the page.

The sample images are in `samples/` (regenerate with `python samples/generate_samples.py`).

## Tests

```powershell
# no extra tools needed
python -m unittest discover -s tests -t . -v

# or, with pytest (installed by requirements.txt)
python -m pytest -v
```

**The most important test** sends the real `samples/even_odd.png` to the real `qwen2.5vl:3b`:

```powershell
python -m unittest tests.test_even_odd_live -v
```

It asserts exactly one vision request, the extracted logic (`Input n`, decision `n % 2 == 0`,
Yes → Even, No → Odd), that the generated Python prints `Even`/`Odd` correctly for 4, 7, 0 and -3,
that verification is `VERIFIED`, and that the run was saved to history. It is **skipped** when
Ollama or the model is unavailable, so make sure it says `ok`, not `skipped`, on the demo machine.

Other suites use an injected fake Ollama client so they can prove, without a GPU, that: only one
vision request is made (and no retry after a malformed answer), the actual uploaded pixels reach
the model, and the generated code changes when the model's answer changes.

## Project structure

```
syntax-ai/
├── backend/
│   ├── app.py               FastAPI routes (thin)
│   ├── config.py            settings / environment variables
│   ├── errors.py            user-safe exception types
│   ├── image_validation.py  real-content image checks + preparation
│   ├── ollama_client.py     Ollama HTTP client (status + the single vision request)
│   ├── vision.py            the vision prompt
│   ├── parser.py            validates the model's JSON into a trusted IR
│   ├── exprs.py             restricted expression parser (blocks code injection)
│   ├── flow.py              flowchart -> structured program (if/else, while loops) + renderers
│   ├── scaffold.py          UML / architecture skeleton generators
│   ├── generator.py         language dispatch
│   ├── security.py          heuristic security analysis
│   ├── verifier.py          compile, run, compare with a flowchart simulation
│   ├── database.py          SQLite history
│   ├── jobs.py              job store for live progress
│   └── pipeline.py          the end-to-end flow
├── frontend/                index.html, styles.css, app.js (no framework)
├── samples/                 even_odd.png, sum_loop.png, grade_check.png (+ generator script)
├── tests/
├── requirements.txt  .env.example  .gitignore  README.md
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | app health |
| GET | `/api/ai-status` | Ollama reachable? model installed? |
| POST | `/api/generate` | multipart `image`, `language`, `requirement` → `202 {job_id}` |
| GET | `/api/jobs/{id}` | live stage, and the result or a clean error |
| GET/DELETE | `/api/history`, `/api/history/{id}` | list / load / delete history |

## How the pieces behave

* **One vision request.** Ollama is asked once per diagram. Code generation, security scanning and
  verification are local and deterministic. A failed or malformed answer is *not* silently retried.
* **No invented logic.** If the model's JSON is malformed, inconsistent, or a decision has no clear
  Yes/No branches, you get *"Unable to reliably interpret this diagram. Please upload a clearer image."*
* **Safe generation.** Every condition/assignment is parsed into a restricted expression grammar
  (numbers, variables, arithmetic, comparisons, and/or/not). Anything else is rejected or left as a
  comment, so text in an image can never inject code. Strings are escaped per language.
* **Honest verification.** `VERIFIED` = compiled, ran under a timeout, and produced the same output
  as an independent simulation of the flowchart graph on several inputs (including boundary values
  around the constants in the diagram). UML/architecture skeletons only get `SYNTAX OK`. Missing
  toolchain → `UNVERIFIED`. Failures show as `FAILED` with the reason.
* **Heuristic security analysis** is a pattern scanner (eval/exec/os.system/shell=True, hard-coded
  passwords and keys, obvious SQL string building, a few C/Java/JS risks). It is not a professional
  SAST tool and can miss issues or flag false positives.

## Known limitations

* **Extraction quality is the model's.** A 3B vision model can misread small text, skip arrows or
  miss Yes/No labels. The app rejects such output instead of guessing, so use large, clean,
  high-contrast diagrams with labelled Yes/No branches. Speed on CPU depends on your hardware.
* **Flowchart text must be code-like.** Conditions such as `n % 2 == 0`, `marks >= 40`, `i <= n` work;
  a handful of plain-English forms (`n is even`, `x greater than y`) are recognised, other prose is
  rejected. Steps that are not assignments become `TODO` comments rather than made-up code.
* **Types are inferred**: variables default to `int`; anything involved in `/` or float literals
  becomes `double`; a few names (`name`, `text`, …) become strings. String comparisons are not supported.
* **Structured code from graphs**: `if/else`, `else if` chains, `while` and do-while style loops with a
  single exit are supported. Jumps into outer loops, loops with several exits and `for`-style
  constructs are not; such diagrams may be rejected or produce code that verification flags.
* **UML/architecture** output is a skeleton: only classes, members, inheritance and components/connections
  that are visible; method bodies are placeholders. Generic types (`List<Dog>`) fall back to a generic
  type. Relationships other than inheritance/implements are recorded as comments.
* **Requirement box**: recorded as a comment in the code; it does not change generation.
* **Verification runs generated code** in a temp folder with a timeout and a reduced environment. It is not
  a hardened sandbox. It only ever runs code generated from the validated grammar, never uploaded files.
* Single-user local app: no authentication, in-memory job list (cleared on restart), history in SQLite.
