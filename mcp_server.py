"""
mcp_server.py
--------------
Exposes the migration QA toolset over the Model Context Protocol, so
any MCP-compatible client (Claude Desktop, Claude Code, a custom
agent, etc.) can discover and call these tools without any custom
integration code.

This file does not contain any new QA logic — it is a thin wrapper
around core/*.py. That's the point of MCP: package what you already
built as standardized, reusable tools.

Run:
    pip install fastmcp
    export GEMINI_API_KEY=...        (or $env:GEMINI_API_KEY=... on PowerShell)
    python mcp_server.py

NOTE: the FastMCP helper class used to live inside the `mcp` package
itself (mcp.server.fastmcp). It has since moved to its own standalone
package — install `fastmcp`, not `mcp[cli]`, and import from
`fastmcp` directly, as below. Don't `pip install mcp` alone; it no
longer ships this submodule.
"""

import json
from pathlib import Path

import core.config  # noqa: F401  — loads .env before any tool reads GEMINI_API_KEY

from fastmcp import FastMCP

from core.data_validator import run_data_validation
from core.visual_validator import run_visual_validation, revalidate_region
from core.ai_analyzer import run_ai_analysis_with_rag
from core.ocr_tool import extract_title, image_to_table

mcp = FastMCP("migration-qa")


@mcp.tool()
def validate_data(spotfire_excel_path: str, powerbi_excel_path: str) -> dict:
    """
    Compare a Spotfire Excel export against a Power BI Excel export.
    Deterministic — matches rows by Order ID, checks structure, rows,
    and values with numeric tolerance. Returns a structured result
    including overall_result: PASS or FAIL.
    """
    return run_data_validation(spotfire_excel_path, powerbi_excel_path)


@mcp.tool()
def validate_visual(spotfire_image_path: str, powerbi_image_path: str) -> dict:
    """
    Compare a Spotfire dashboard screenshot against a Power BI dashboard
    screenshot using a vision-language model. Checks title, nav bar,
    visual count/type/position, filters, labels, tables, and overall
    layout. Returns per-check status, severity, and confidence.
    """
    return run_visual_validation(spotfire_image_path, powerbi_image_path)


@mcp.tool()
def revalidate_visual_region(
    spotfire_image_path: str,
    powerbi_image_path: str,
    left: float,
    top: float,
    right: float,
    bottom: float,
    focus_category: str,
) -> dict:
    """
    Re-checks ONE region of the two screenshots at higher effective
    resolution. Use this when a check from validate_visual comes back
    with confidence below ~0.7 — full-page images can lose fine detail
    (small text, thin gridlines) to downsampling; a cropped, upscaled
    region recovers it. Coordinates are fractional (0.0-1.0) relative
    to image size: left, top, right, bottom.
    """
    return revalidate_region(
        spotfire_image_path,
        powerbi_image_path,
        box=(left, top, right, bottom),
        focus_category=focus_category,
    )


@mcp.tool()
def analyze_results(data_qa_json: str, visual_qa_json: str) -> dict:
    """
    Runs the LLM reasoning layer over a data QA result and a visual QA
    result (each passed as a JSON string), automatically grounded in
    relevant past cases and migration rules from the knowledge base
    (RAG). Returns overall status, risk level, critical findings, and
    prioritized actions.
    """
    data_qa = json.loads(data_qa_json)
    visual_qa = json.loads(visual_qa_json)
    return run_ai_analysis_with_rag(data_qa, visual_qa)


@mcp.tool()
def ocr_extract_report(image_path: str) -> dict:
    """
    Fallback path for when there is no clean Excel export — extracts
    the report title and a best-effort table from a screenshot or
    exported image using OCR (Tesseract, local, no API cost). Less
    reliable than a real Excel export; use validate_data() when a
    clean export is available.
    """
    return {
        "title": extract_title(image_path),
        "table": image_to_table(image_path).to_dict(orient="records"),
    }


@mcp.tool()
def run_full_pipeline(
    spotfire_excel_path: str,
    powerbi_excel_path: str,
    spotfire_image_path: str,
    powerbi_image_path: str,
) -> dict:
    """
    Convenience tool that runs the entire pipeline in one call:
    data validation -> visual validation -> AI analysis. Equivalent to
    what qa_orchestrator.py does, but callable as a single MCP tool.
    """
    data_qa = run_data_validation(spotfire_excel_path, powerbi_excel_path)
    visual_qa = run_visual_validation(spotfire_image_path, powerbi_image_path)
    analysis = run_ai_analysis_with_rag(data_qa, visual_qa)

    return {
        "data_qa": data_qa,
        "visual_qa": visual_qa,
        "analysis": analysis,
    }


if __name__ == "__main__":
    mcp.run()
