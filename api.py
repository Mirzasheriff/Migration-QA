"""
api.py
-------
FastAPI backend for the Migration QA Assistant. Wraps the same core
functions used by the CLI scripts, the Agent, and the MCP server — no
new QA logic lives here, only HTTP plumbing. This is what the frontend
(frontend/index.html) talks to.

Run:
    pip install fastapi uvicorn python-multipart
    python api.py
Then open frontend/index.html directly in a browser (double-click it —
no server needed for the frontend itself). It talks to this backend at
http://127.0.0.1:8000 by default.

Docs: once running, visit http://127.0.0.1:8000/docs for Swagger UI —
useful for testing endpoints by hand before wiring up the frontend.
"""

import json
import shutil
import tempfile
from pathlib import Path

import core.config  # noqa: F401  — loads .env before anything reads GEMINI_API_KEY

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.data_validator import run_data_validation
from core.visual_validator import run_visual_validation
from core.ai_analyzer import run_ai_analysis_with_rag
from core.rag import retrieve

import agent as agent_module

app = FastAPI(title="Migration QA Assistant API")

# Local-demo CORS setting: wide open so the frontend works whether
# it's opened as a file:// page or served some other way. Tighten
# this (specific origins only) before deploying this anywhere real.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _save_upload(upload: UploadFile, dest_dir: Path) -> str:
    dest_path = dest_dir / upload.filename
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(upload.file, f)
    return str(dest_path)


@app.get("/")
def root():
    return {
        "service": "Migration QA Assistant API",
        "docs": "/docs",
        "health": "/health",
        "note": "This is the backend only. Open frontend/index.html directly in your browser to use the dashboard.",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/validate/data")
async def validate_data(
    spotfire_excel: UploadFile = File(...),
    powerbi_excel: UploadFile = File(...),
):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        spot_path = _save_upload(spotfire_excel, tmp_path)
        pbi_path = _save_upload(powerbi_excel, tmp_path)
        try:
            return run_data_validation(spot_path, pbi_path, output_excel_path=None)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.post("/validate/visual")
async def validate_visual(
    spotfire_image: UploadFile = File(...),
    powerbi_image: UploadFile = File(...),
):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        spot_path = _save_upload(spotfire_image, tmp_path)
        pbi_path = _save_upload(powerbi_image, tmp_path)
        try:
            return run_visual_validation(spot_path, pbi_path)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))


class AnalyzeRequest(BaseModel):
    data_qa: dict
    visual_qa: dict


@app.post("/analyze")
async def analyze(payload: AnalyzeRequest):
    try:
        return run_ai_analysis_with_rag(payload.data_qa, payload.visual_qa)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class RagQueryRequest(BaseModel):
    query: str
    top_k: int = 3


@app.post("/rag/query")
async def rag_query(payload: RagQueryRequest):
    try:
        return {"results": retrieve(payload.query, top_k=payload.top_k)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/agent/run")
async def agent_run(
    spotfire_excel: UploadFile = File(...),
    powerbi_excel: UploadFile = File(...),
    spotfire_image: UploadFile = File(...),
    powerbi_image: UploadFile = File(...),
):
    """Non-streaming: runs the full agent loop, returns the complete
    result (data_qa, visual_qa, analysis, trace) in one response."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        paths = {
            "spotfire_excel_path": _save_upload(spotfire_excel, tmp_path),
            "powerbi_excel_path": _save_upload(powerbi_excel, tmp_path),
            "spotfire_image_path": _save_upload(spotfire_image, tmp_path),
            "powerbi_image_path": _save_upload(powerbi_image, tmp_path),
        }
        try:
            return agent_module.run_agent(**paths)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.post("/agent/run/stream")
async def agent_run_stream(
    spotfire_excel: UploadFile = File(...),
    powerbi_excel: UploadFile = File(...),
    spotfire_image: UploadFile = File(...),
    powerbi_image: UploadFile = File(...),
):
    """
    Streaming version: Server-Sent Events, one JSON line per trace
    entry, as the agent produces them — this is what powers the live
    console in the frontend. The temp upload directory is kept alive
    for the lifetime of the generator and cleaned up in `finally`.
    """
    tmp_dir = tempfile.mkdtemp()
    tmp_path = Path(tmp_dir)

    paths = {
        "spotfire_excel_path": _save_upload(spotfire_excel, tmp_path),
        "powerbi_excel_path": _save_upload(powerbi_excel, tmp_path),
        "spotfire_image_path": _save_upload(spotfire_image, tmp_path),
        "powerbi_image_path": _save_upload(powerbi_image, tmp_path),
    }

    def event_generator():
        try:
            for entry in agent_module.run_agent_stream(**paths):
                yield f"data: {json.dumps(entry, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
