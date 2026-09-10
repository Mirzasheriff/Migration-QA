"""
smoke_test.py
--------------
Run this in YOUR environment (where GEMINI_API_KEY is already set) to
verify every new piece actually works against the live API before the
demo. Does not modify any of your existing result files.

Usage:
    export GEMINI_API_KEY=your_key_here      # if not already set
    python smoke_test.py
"""

import json
import os
import sys
import traceback

import core.config  # noqa: F401  — loads .env FIRST, before any os.getenv() check below

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results = []


def check(name, fn):
    print(f"\n--- {name} ---")
    try:
        out = fn()
        print(f"[{PASS}] {name}")
        results.append((name, PASS, None))
        return out
    except Exception as e:
        print(f"[{FAIL}] {name}")
        traceback.print_exc()
        results.append((name, FAIL, str(e)))
        return None


def test_data_validator():
    from core.data_validator import run_data_validation

    result = run_data_validation(
        "spotfire_reference.xlsx", "powerbi_migrated.xlsx", output_excel_path=None
    )
    assert result["overall_result"] == "FAIL", "expected FAIL on the known-defective test files"
    print(json.dumps(result, indent=2)[:500])
    return result


def test_ocr():
    from core.ocr_tool import extract_title

    title = extract_title("spotfire_reference.png")
    print("OCR title read:", title[:80])
    assert len(title) > 0
    return title


def test_rag():
    from core.rag import retrieve

    hits = retrieve("sales KPI shows a different number than Spotfire", top_k=2)
    print(json.dumps(hits, indent=2))
    assert len(hits) > 0
    return hits


def test_visual_validator():
    if not os.getenv("GEMINI_API_KEY"):
        print("SKIPPED — no GEMINI_API_KEY set")
        results.append(("visual_validator (live API)", SKIP, "no API key"))
        return None
    from core.visual_validator import run_visual_validation

    result = run_visual_validation("spotfire_reference.png", "powerbi_defective.png")
    print(json.dumps(result, indent=2)[:800])
    assert "overall_status" in result
    return result


def test_ai_analyzer(data_qa, visual_qa):
    if not os.getenv("GEMINI_API_KEY") or data_qa is None or visual_qa is None:
        print("SKIPPED — missing API key or prior results")
        results.append(("ai_analyzer (live API)", SKIP, "missing key or prior results"))
        return None
    from core.ai_analyzer import run_ai_analysis_with_rag

    result = run_ai_analysis_with_rag(data_qa, visual_qa)
    print(json.dumps(result, indent=2)[:800])
    assert "overall_status" in result
    return result


def test_agent():
    if not os.getenv("GEMINI_API_KEY"):
        print("SKIPPED — no GEMINI_API_KEY set")
        results.append(("agent (live API)", SKIP, "no API key"))
        return None
    from agent import run_agent, print_trace

    result = run_agent(
        "spotfire_reference.xlsx",
        "powerbi_migrated.xlsx",
        "spotfire_reference.png",
        "powerbi_defective.png",
    )
    print_trace(result["trace"])
    assert result["analysis"] is not None, "agent did not reach a final analysis"
    return result


def test_mcp_server_imports():
    import mcp_server  # noqa: F401  — just confirm it imports and tools register

    tool_count = None
    for attr in ("_tool_manager", "_tools"):
        obj = getattr(mcp_server.mcp, attr, None)
        if obj is not None:
            tools = getattr(obj, "_tools", obj)
            try:
                tool_count = len(tools)
            except TypeError:
                pass
            break
    print("MCP server module imported OK. Registered tools:", tool_count)
    return tool_count


def main():
    print("=" * 60)
    print("  MIGRATION QA — SMOKE TEST")
    print("=" * 60)

    data_qa = check("Data Validator (deterministic)", test_data_validator)
    check("OCR (local, Tesseract)", test_ocr)
    check("RAG retrieval (local/TF-IDF or Gemini)", test_rag)
    visual_qa = check("Visual Validator (live Gemini API)", test_visual_validator)
    check("AI Analyzer (live Gemini API)", lambda: test_ai_analyzer(data_qa, visual_qa))
    check("Agent loop (live Gemini API, multi-step)", test_agent)
    check("MCP server (import + tool registration)", test_mcp_server_imports)

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    for name, status, err in results:
        line = f"  [{status}] {name}"
        if err and status == FAIL:
            line += f"  -- {err}"
        print(line)

    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    print(f"\n{len(results)} checks run, {n_fail} failed.")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
