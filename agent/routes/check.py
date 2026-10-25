"""POST /api/check: free text in, verified verdict out.

text -> Gemini extracts the claim -> check_claim (ChEMBL, plain code) -> Gemini explains the verdict.
Put this file in agent/routes/check.py.
"""
import asyncio
import json
import re

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent.ai.gemini import ask_gemini
from agent.drugcheck.checker import check_claim

EXTRACT_PROMPT = """Extract the drug-target potency claim from the text below.
Reply with ONLY a JSON object: {"drug": str|null, "target": str|null, "activity_type": "IC50"|"Ki"|"Kd"|"EC50"|null, "value": number|null, "units": "pM"|"nM"|"uM"|"mM"|null}
Write micromolar as "uM". If the text contains no drug-target claim, use null for drug.

Text: """


class CheckRequest(BaseModel):
    text: str = ""
    drug: str | None = None
    target: str | None = None
    activity_type: str | None = None
    value: float | None = None
    units: str | None = None


async def _ask_retry(prompt: str, tries: int = 2, timeout: float = 12.0) -> str:
    for i in range(tries):
        try:
            return await asyncio.wait_for(ask_gemini(prompt), timeout=timeout)
        except Exception:
            if i == tries - 1:
                raise
            await asyncio.sleep(1)

def _plain_summary(result: dict) -> str:
    """Deterministic fallback explanation, no LLM needed."""
    s, ex = result.get("stats", {}), result.get("excluded", {})
    parts = [f"Verdict: {result['verdict']}."]
    if s:
        parts.append(
            f"{s['n_usable']} clean human wild-type measurements from {s['n_documents']} papers "
            f"(median {s['median_nM']:g} nM, range {s['min_nM']:g} to {s['max_nM']:g} nM)."
        )
    if ex:
        parts.append("Excluded: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in ex.items()) + ".")
    if result.get("flags"):
        parts.append("Flags: " + ", ".join(result["flags"]) + ".")
    return " ".join(parts)


def register(app: FastAPI) -> None:
    @app.post("/api/check")
    async def check(req: CheckRequest) -> dict:
        
        if req.drug and req.target:
            claim = {"drug": req.drug, "target": req.target, "activity_type": req.activity_type,
                     "value": req.value, "units": req.units}
        else:
            raw = await _ask_retry(EXTRACT_PROMPT + req.text)
            raw = re.sub(r"```(?:json)?", "", raw)
            try:
                claim = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
            except json.JSONDecodeError:
                raise HTTPException(422, "Could not read a claim from that text")
        if not claim.get("drug") or not claim.get("target"):
            raise HTTPException(422, "No drug-target claim found in that text")

        # check_claim does blocking HTTP, so keep it off the event loop
        try:
            result = await asyncio.to_thread(
                check_claim,
                claim["drug"], claim["target"], claim.get("activity_type"),
                claim.get("value"), claim.get("units") or "nM",
            )
        except Exception as e:
            raise HTTPException(502, f"check_claim failed: {type(e).__name__}: {e}")
        try:
            explanation = await _ask_retry(
                "Explain this verification result to a scientist in 3-4 sentences. Use only the data given, "
                "add no outside facts, and mention what was excluded and why.\n"
                f"Original claim text: {req.text}\nResult JSON: {json.dumps(result)[:6000]}"
            )
        except Exception:
            explanation = _plain_summary(result)
        
        return {"claim": claim, "result": result, "explanation": explanation}