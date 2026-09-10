# Migration QA Assistant — FastAPI + Frontend

## Setup (one-time)

```powershell
pip install -r requirements.txt
```

Make sure your `.env` file (in this folder) has a real key:
```
GEMINI_API_KEY=your_real_key_here
```

## Running it

**Terminal 1 — start the backend:**
```powershell
python api.py
```
Leave this running. It listens on `http://127.0.0.1:8000`.
Visit `http://127.0.0.1:8000/docs` to test endpoints by hand (Swagger UI)
before touching the frontend at all — good first check.

**Then — open the frontend:**
Just double-click `frontend/index.html` (or right-click → Open with →
your browser). No server needed for this part; it's a single static
file that calls the backend directly.

## Using it

1. Upload the 4 files: Spotfire Excel, Power BI Excel, Spotfire
   screenshot, Power BI screenshot (your existing test files work —
   `spotfire_reference.xlsx/png`, `powerbi_migrated.xlsx`,
   `powerbi_defective.png`)
2. Click **Run agent pipeline**
3. Watch the live trace console — same step-by-step log format you've
   seen in the terminal, now streaming into the browser in real time
4. Results populate below as each stage finishes: Data QA, Visual QA,
   then the RAG-grounded AI Analysis with priority actions

## API endpoints, if you want to call them directly

| Endpoint | What it does |
|---|---|
| `GET /health` | Quick check the backend is up |
| `POST /validate/data` | Upload 2 Excel files → data QA result |
| `POST /validate/visual` | Upload 2 images → visual QA result |
| `POST /analyze` | JSON body `{data_qa, visual_qa}` → RAG-grounded analysis |
| `POST /rag/query` | JSON body `{query, top_k}` → relevant knowledge-base records |
| `POST /agent/run` | Upload all 4 files → full result in one response (no streaming) |
| `POST /agent/run/stream` | Same as above, but streamed live (what the frontend uses) |

## Troubleshooting

- **Frontend shows nothing when you click Run** → check the backend
  terminal for errors, and confirm `python api.py` is actually running.
- **CORS error in browser console** → shouldn't happen (CORS is wide
  open for local dev in `api.py`), but if it does, confirm you're
  hitting `http://127.0.0.1:8000` and not `localhost:8000` — some
  browsers treat those as different origins.
- **429 rate limit mid-demo** → see the earlier quota discussion; this
  is a Google API limit, not a bug here.
