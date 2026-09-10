# Migration QA Assistant

**AI-assisted QA for Spotfire → Power BI dashboard migrations.** Catches missing rows, value mismatches, and visual regressions in minutes instead of a manual, eyeball-and-VLOOKUP pass — and explains *why* something differs, not just *that* it differs.

## The problem

Validating a BI migration usually means two tedious jobs done by hand: comparing exported Excel tables cell-by-cell, and eyeballing two dashboard screenshots to see if the charts, filters, and layout still match. Both are repetitive, error-prone, and don't scale past a handful of reports.

## How it's built

The project deliberately splits work between deterministic code and AI, instead of reaching for AI everywhere:

| Layer | What it does | Why this approach |
|---|---|---|
| **Data validation** (`core/data_validator.py`) | Pure pandas/numpy row-and-value diff, keyed on `Order ID` | Numbers need to match *exactly* — no AI, no randomness, fully repeatable |
| **Visual validation** (`core/visual_validator.py`) | Sends both dashboard screenshots to Gemini (VLM), gets back structured JSON per category (title, nav, chart type/position, filters, tables, layout) | "Does this look the same" isn't something you can write exact-match code for |
| **AI reasoning** (`core/ai_analyzer.py`) | Takes both results, explains what's wrong and what to prioritize, grounded in past cases via RAG | Turns raw diffs into something a tester can act on |
| **Agent** (`agent.py`) | Given a goal and a toolbox, decides *which* tool to call next — including re-zooming into a screenshot region when visual confidence is low, or falling back to OCR when there's no clean Excel export | Fixed pipelines don't adapt; an agent can route around missing/ambiguous input |

Supporting pieces:
- **RAG** (`core/rag.py`, `core/embeddings.py`, `core/vector_store.py`) — retrieves similar past defects/rules from `knowledge_base.json` to ground the AI analysis in precedent. Embeddings fall back automatically from Gemini to a local TF-IDF vectorizer, so RAG still works fully offline with no API key.
- **OCR fallback** (`core/ocr_tool.py`) — when there's no clean Excel export, extracts table-shaped text from a screenshot or PDF via Tesseract and feeds it into the same `data_validator` logic.
- **Retry/backoff** (`core/retry.py`) — wraps every Gemini call so a transient `503`/`429` self-heals mid-demo instead of failing the run.
- **MCP server** (`mcp_server.py`) — exposes the same tools over the Model Context Protocol so any MCP-compatible client (Claude Desktop, Claude Code, a custom agent) can call them without bespoke integration code.
- **FastAPI backend + static frontend** (`api.py`, `frontend/index.html`) — a real UI on top of the same core logic, with a live streaming endpoint for watching the agent's trace in real time.

## Project structure

```
migration-qa/
├── core/
│   ├── config.py           # centralized .env loading (graceful fallback if python-dotenv is missing)
│   ├── data_validator.py   # deterministic Excel-vs-Excel comparison
│   ├── visual_validator.py # Gemini VLM screenshot-vs-screenshot comparison
│   ├── ai_analyzer.py      # RAG-grounded reasoning layer
│   ├── rag.py               # retrieval over knowledge_base.json
│   ├── embeddings.py        # Gemini / TF-IDF embedding backends
│   ├── vector_store.py      # cosine-similarity index (auto-rebuilds — not committed)
│   ├── ocr_tool.py          # Tesseract OCR fallback for non-Excel input
│   └── retry.py             # exponential backoff for Gemini calls
├── agent.py                 # tool-using agent, decides its own next step
├── mcp_server.py             # thin MCP wrapper around core/*.py
├── api.py                    # FastAPI backend
├── frontend/index.html       # static single-file frontend
├── smoke_test.py             # fast end-to-end sanity check
├── knowledge_base.json       # source RAG records (rules + past defect cases)
├── legacy/qa_orchestrator.py # earlier fixed-order pipeline, kept for before/after comparison
├── spotfire_reference.xlsx/png, powerbi_migrated.xlsx/png, powerbi_defective.png
│                              # FAIL-demo fixtures — powerbi_migrated.xlsx has 10 rows
│                              # deliberately missing; make_defective.py paints over
│                              # powerbi_migrated.png to build the broken screenshot
├── spotfire_pixel_match.xlsx/png, powerbi_pixel_match.xlsx/png
│                              # PASS-demo fixtures — a verified, content-identical pair
├── make_defective.py         # generates the deliberately-broken visual fixture
└── requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
```

You'll also need **Tesseract OCR** itself installed (not just the Python wrapper) for the OCR fallback — see the [Windows install guide](https://github.com/UB-Mannheim/tesseract/wiki). `core/ocr_tool.py` auto-detects common install locations, or set `TESSERACT_CMD` if it's somewhere unusual.

Copy `.env.example` to `.env` and add your own key:

```
GEMINI_API_KEY=your_real_key_here
```

Get a key at [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey). **Never commit your real `.env`** — it's already git-ignored.

## Running it

**Option 1 — FastAPI backend + frontend (recommended):**

```bash
python api.py
```

Leaves the backend running on `http://127.0.0.1:8000` (Swagger docs at `/docs`). Then just open `frontend/index.html` in a browser — it's a static file that calls the backend directly, no build step.

Upload the four fixture files (or your own), click **Run agent pipeline**, and watch the live trace as Data QA → Visual QA → AI Analysis complete in sequence.

| Endpoint | What it does |
|---|---|
| `GET /health` | Quick check the backend is up |
| `POST /validate/data` | Upload 2 Excel files → data QA result |
| `POST /validate/visual` | Upload 2 images → visual QA result |
| `POST /analyze` | JSON body `{data_qa, visual_qa}` → RAG-grounded analysis |
| `POST /rag/query` | JSON body `{query, top_k}` → relevant knowledge-base records |
| `POST /agent/run` | Upload all 4 files → full result in one response |
| `POST /agent/run/stream` | Same, streamed live (what the frontend uses) |

**Option 2 — CLI agent directly:**

```bash
python agent.py
```

**Option 3 — as MCP tools** (Claude Desktop, Claude Code, or any MCP client):

```bash
pip install fastmcp
python mcp_server.py
```

**Sanity check anytime:**

```bash
python smoke_test.py
```

## Demo: PASS vs. FAIL

The repo ships two ready-made scenarios so you can show both outcomes without touching real migration data:

- **FAIL** — `spotfire_reference.xlsx/png` vs. `powerbi_migrated.xlsx` (10 rows deliberately missing) and `powerbi_defective.png` (title, KPI value, and a whole chart deliberately altered by `make_defective.py`)
- **PASS** — `spotfire_pixel_match.xlsx/png` vs. `powerbi_pixel_match.xlsx/png`, a verified content-identical pair (0 missing rows, 0 mismatches, confirmed by actually running `core/data_validator.py` against it)

## Troubleshooting

- **Frontend shows nothing on Run** → check the `python api.py` terminal for errors first.
- **CORS error in the browser console** → make sure you're hitting `http://127.0.0.1:8000`, not `localhost:8000` — some browsers treat those as different origins.
- **429 rate limit mid-demo** → a Google API quota limit, not a bug; `core/retry.py` already retries transient errors automatically.

## Roadmap

- [ ] Auto-capture pipeline — automate the screenshot + Excel export step instead of doing it by hand
- [ ] Q&A chatbot over results — ask "what are the 15 extra rows?" and get a plain-English answer pulled from the QA result JSON

## License

MIT — see [LICENSE](LICENSE).
