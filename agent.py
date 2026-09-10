"""
agent.py
---------
The "Agentic AI" piece of the project. qa_orchestrator.py always runs
the same three steps in the same order, no matter what it finds. This
agent instead gives the model a goal and a toolbox, and lets IT decide
which tool to call next based on what it has learned so far — including
deciding to zoom into a screenshot region when visual confidence is
low (the VLM-data-loss mitigation), or falling back to OCR if no clean
Excel export exists.

Every decision is logged to `trace` so the reasoning is visible, not a
black box — useful both for debugging and for the presentation.

Run:
    Put GEMINI_API_KEY=... in a .env file in this folder (loaded
    automatically via core/config.py), or set it as an environment
    variable directly.
    python agent.py
"""

import json
import os
from pathlib import Path

import core.config  # noqa: F401  — loads .env before GEMINI_API_KEY is read below
from core.retry import call_with_retry

from google import genai
from google.genai import types

from core.data_validator import run_data_validation
from core.visual_validator import run_visual_validation, revalidate_region
from core.ai_analyzer import run_ai_analysis_with_rag
from core.ocr_tool import extract_title, image_to_table

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
MAX_STEPS = 8  # hard cap so the agent can't loop forever
LOW_CONFIDENCE_THRESHOLD = 0.7


# ============================================================
# TOOL IMPLEMENTATIONS — same core functions as the MCP server,
# wrapped here so the agent loop can call them by name.
# ============================================================

def _tool_validate_data(args: dict) -> dict:
    return run_data_validation(args["spotfire_excel_path"], args["powerbi_excel_path"])


def _tool_validate_visual(args: dict) -> dict:
    return run_visual_validation(args["spotfire_image_path"], args["powerbi_image_path"])


def _tool_zoom_and_recheck(args: dict) -> dict:
    return revalidate_region(
        args["spotfire_image_path"],
        args["powerbi_image_path"],
        box=(args["left"], args["top"], args["right"], args["bottom"]),
        focus_category=args["focus_category"],
    )


def _tool_ocr_fallback(args: dict) -> dict:
    path = args["image_path"]
    return {"title": extract_title(path), "table": image_to_table(path).to_dict(orient="records")}


def _tool_analyze(args: dict) -> dict:
    return run_ai_analysis_with_rag(args["data_qa"], args["visual_qa"])


TOOL_IMPLS = {
    "validate_data": _tool_validate_data,
    "validate_visual": _tool_validate_visual,
    "zoom_and_recheck": _tool_zoom_and_recheck,
    "ocr_fallback": _tool_ocr_fallback,
    "analyze": _tool_analyze,
}

# ============================================================
# TOOL DECLARATIONS — schema the model uses to decide what to call
# ============================================================

TOOLS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="validate_data",
            description=(
                "Deterministically compare a Spotfire Excel export against a "
                "Power BI Excel export. Use this first if clean Excel files "
                "are available."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "spotfire_excel_path": {"type": "STRING"},
                    "powerbi_excel_path": {"type": "STRING"},
                },
                "required": ["spotfire_excel_path", "powerbi_excel_path"],
            },
        ),
        types.FunctionDeclaration(
            name="validate_visual",
            description=(
                "Compare a Spotfire dashboard screenshot against a Power BI "
                "dashboard screenshot using a vision-language model. Returns "
                "per-check status, severity, and confidence (0-1)."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "spotfire_image_path": {"type": "STRING"},
                    "powerbi_image_path": {"type": "STRING"},
                },
                "required": ["spotfire_image_path", "powerbi_image_path"],
            },
        ),
        types.FunctionDeclaration(
            name="zoom_and_recheck",
            description=(
                "Re-check ONE region of the two screenshots at higher "
                "effective resolution. Call this when a check from "
                "validate_visual came back with confidence below 0.7 — "
                "full-page images can lose fine detail to downsampling. "
                "left/top/right/bottom are fractional coordinates 0.0-1.0."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "spotfire_image_path": {"type": "STRING"},
                    "powerbi_image_path": {"type": "STRING"},
                    "left": {"type": "NUMBER"},
                    "top": {"type": "NUMBER"},
                    "right": {"type": "NUMBER"},
                    "bottom": {"type": "NUMBER"},
                    "focus_category": {"type": "STRING"},
                },
                "required": [
                    "spotfire_image_path",
                    "powerbi_image_path",
                    "left",
                    "top",
                    "right",
                    "bottom",
                    "focus_category",
                ],
            },
        ),
        types.FunctionDeclaration(
            name="ocr_fallback",
            description=(
                "Extract a title and best-effort table from an image using "
                "OCR. Only use this if no clean Excel export is available."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {"image_path": {"type": "STRING"}},
                "required": ["image_path"],
            },
        ),
        types.FunctionDeclaration(
            name="analyze",
            description=(
                "Run the final reasoning pass once BOTH data_qa and "
                "visual_qa results are available. Automatically grounds "
                "the analysis in relevant past cases and migration rules "
                "retrieved from the knowledge base (RAG) before "
                "reasoning. Produces the prioritized findings and "
                "recommended actions. Call this LAST."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "data_qa": {"type": "OBJECT"},
                    "visual_qa": {"type": "OBJECT"},
                },
                "required": ["data_qa", "visual_qa"],
            },
        ),
    ]
)

SYSTEM_INSTRUCTION = f"""
You are the autonomous QA agent for a Spotfire-to-Power BI migration.

You have four files available:
- spotfire_excel_path, powerbi_excel_path (clean Excel exports)
- spotfire_image_path, powerbi_image_path (dashboard screenshots)

Goal: produce a final, prioritized QA analysis.

Decide the plan yourself. A sensible default plan is:
1. validate_data — call this exactly ONCE.
2. validate_visual — call this exactly ONCE for the full-page
   comparison. Do NOT call it again after this — it is expensive and
   deterministic given the same two images, so repeating it wastes
   API calls without producing new information.
3. For any check in validate_visual's results with confidence below
   {LOW_CONFIDENCE_THRESHOLD}, call zoom_and_recheck on that SPECIFIC
   region instead — this is the correct tool for refining a
   low-confidence read, not re-running validate_visual wholesale.
4. If Excel files are unavailable, use ocr_fallback instead of
   validate_data.
5. Once you have both a data QA result and a (possibly refined) visual
   QA result, call analyze exactly once, then stop and summarize.

You may deviate from this plan if the situation calls for it, but
explain your reasoning briefly before each tool call.
"""


def run_agent_stream(
    spotfire_excel_path: str,
    powerbi_excel_path: str,
    spotfire_image_path: str,
    powerbi_image_path: str,
    client: genai.Client | None = None,
):
    """
    Generator version of the agent loop — yields each trace entry as it
    happens (tool_call, tool_result, final_text, stopping), and yields
    exactly one final entry of type "done" carrying the complete state.

    This is what FastAPI's streaming endpoint consumes to push live
    progress to the frontend. run_agent() below is a thin wrapper that
    drains this generator for callers (like smoke_test.py) that just
    want the final dict, same as before this refactor.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set.")
    client = client or genai.Client(api_key=api_key)

    state = {"data_qa": None, "visual_qa": None, "analysis": None}

    user_message = (
        "Begin the QA process for this migration.\n"
        f"spotfire_excel_path: {spotfire_excel_path}\n"
        f"powerbi_excel_path: {powerbi_excel_path}\n"
        f"spotfire_image_path: {spotfire_image_path}\n"
        f"powerbi_image_path: {powerbi_image_path}\n"
    )

    contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_message)])]

    for step in range(1, MAX_STEPS + 1):
        retries_this_call = []
        response = call_with_retry(
            lambda: client.models.generate_content(
                model=MODEL_NAME,
                contents=contents,
                config=types.GenerateContentConfig(
                    tools=[TOOLS],
                    system_instruction=SYSTEM_INSTRUCTION,
                ),
            ),
            on_retry=lambda attempt, delay, e: retries_this_call.append(
                {"attempt": attempt, "delay": delay, "error": str(e)}
            ),
        )

        for r in retries_this_call:
            yield {
                "step": step,
                "type": "retry",
                "attempt": r["attempt"],
                "delay": r["delay"],
                "error": r["error"],
            }

        candidate = response.candidates[0]
        contents.append(candidate.content)

        function_calls = [
            part.function_call for part in candidate.content.parts if part.function_call
        ]

        if not function_calls:
            final_text = "".join(
                part.text for part in candidate.content.parts if part.text
            )
            yield {"step": step, "type": "final_text", "content": final_text}
            break

        response_parts = []
        for fc in function_calls:
            tool_name = fc.name
            args = dict(fc.args)

            yield {"step": step, "type": "tool_call", "tool": tool_name, "args": args}

            if tool_name == "analyze":
                args.setdefault("data_qa", state["data_qa"])
                args.setdefault("visual_qa", state["visual_qa"])

            # Hard guard (not just a prompt instruction): validate_data
            # and validate_visual are deterministic given the same
            # inputs, so a repeat call reuses the cached result instead
            # of burning another API call — this matters especially for
            # validate_visual, which is the expensive vision call.
            if tool_name == "validate_data" and state["data_qa"] is not None:
                result = {**state["data_qa"], "_cached": True}
            elif tool_name == "validate_visual" and state["visual_qa"] is not None:
                result = {**state["visual_qa"], "_cached": True}
            else:
                try:
                    result = TOOL_IMPLS[tool_name](args)
                except Exception as e:
                    result = {"error": str(e)}

            if tool_name == "validate_data":
                state["data_qa"] = result
            elif tool_name == "validate_visual":
                state["visual_qa"] = result
            elif tool_name == "analyze" and "error" not in result:
                # Only a genuine analysis result marks the run complete —
                # a caught exception (network blip, bad JSON from the
                # model, etc.) must not look like "analysis complete" to
                # the stop condition below. Let the model see the error
                # via the function response and decide whether to retry.
                state["analysis"] = result

            yield {"step": step, "type": "tool_result", "tool": tool_name, "result": result}

            response_parts.append(
                types.Part.from_function_response(name=tool_name, response={"result": result})
            )

        contents.append(types.Content(role="user", parts=response_parts))

        if state["analysis"] is not None:
            yield {"step": step, "type": "stopping", "reason": "analysis complete"}
            break
    else:
        yield {"step": MAX_STEPS, "type": "stopping", "reason": "max steps reached"}

    yield {
        "type": "done",
        "data_qa": state["data_qa"],
        "visual_qa": state["visual_qa"],
        "analysis": state["analysis"],
    }


def run_agent(
    spotfire_excel_path: str,
    powerbi_excel_path: str,
    spotfire_image_path: str,
    powerbi_image_path: str,
    client: genai.Client | None = None,
) -> dict:
    """
    Runs the agent loop to completion (or MAX_STEPS) and returns a dict
    with the final analysis plus the full decision trace. Same public
    signature and return shape as before — just implemented on top of
    run_agent_stream() now, so streaming and non-streaming callers
    share one code path.
    """
    trace = []
    final_state = {"data_qa": None, "visual_qa": None, "analysis": None}

    for entry in run_agent_stream(
        spotfire_excel_path, powerbi_excel_path, spotfire_image_path, powerbi_image_path, client
    ):
        if entry["type"] == "done":
            final_state = entry
        else:
            trace.append(entry)

    return {
        "data_qa": final_state["data_qa"],
        "visual_qa": final_state["visual_qa"],
        "analysis": final_state["analysis"],
        "trace": trace,
    }


def print_trace(trace: list[dict]) -> None:
    print("\n" + "=" * 60)
    print("             AGENT DECISION TRACE")
    print("=" * 60)
    for entry in trace:
        if entry["type"] == "tool_call":
            print(f"\n[step {entry['step']}] -> calling {entry['tool']}({entry['args']})")
        elif entry["type"] == "tool_result":
            print(f"[step {entry['step']}] <- {entry['tool']} returned result")
        elif entry["type"] == "final_text":
            print(f"\n[step {entry['step']}] Agent final message:\n{entry['content']}")
        elif entry["type"] == "stopping":
            print(f"\n[step {entry['step']}] Stopped: {entry['reason']}")


def main():
    result = run_agent(
        "spotfire_reference.xlsx",
        "powerbi_migrated.xlsx",
        "spotfire_reference.png",
        "powerbi_defective.png",
    )
    print_trace(result["trace"])

    with open("agent_result.json", "w") as f:
        json.dump(result, f, indent=2, default=str)

    print("\nFull result saved to agent_result.json")


if __name__ == "__main__":
    main()
