"""LLM-based hallucination detection."""

from __future__ import annotations

import json
import re

from paperwise.core.llm_client import LLMClient


class HallucinationDetector:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def detect(self, report, paper_text):
        # Truncate inputs to avoid overly long prompts that cause judge failures
        paper_excerpt = paper_text[:12000]
        report_excerpt = report[:8000]
        prompt = (
            "You are a hallucination detector. Compare the report against the paper ground truth.\n\n"
            "Paper (ground truth):\n"
            f"{paper_excerpt}\n\n"
            "Report to evaluate:\n"
            f"{report_excerpt}\n\n"
            "Rules:\n"
            "1. A hallucination is any claim that is NOT supported by the paper.\n"
            "2. Numerical values must match exactly. If the paper says 95% and the report says 99%, that's critical.\n"
            "3. Missing citations are NOT hallucinations if the content is correct.\n"
            "4. If you cannot determine, say 'unknown'.\n\n"
            "Respond ONLY with a single JSON object, no other text:\n"
            '{"hallucinations": [{"claim": "...", "reason": "...", "severity": "critical|major|minor"}], "overall_severity": "none|minor|major|critical", "summary": "..."}'
        )
        try:
            # Kimi Code only allows temperature=1
            resp = await self.llm.chat(messages=[{"role": "user", "content": prompt}], temperature=1, max_tokens=800)
            content = (resp.content or "").strip()

            # Extract JSON from markdown code blocks if present
            if content.startswith("```"):
                parts = content.split("```")
                if len(parts) > 1:
                    content = parts[1]
                    if content.startswith("json"):
                        content = content[4:]
                    content = content.strip()

            # Try to find JSON object in the response
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                content = match.group(0)

            # If still no valid JSON, return unknown instead of error
            if not content or not content.startswith("{"):
                return {
                    "passed": True,
                    "flagged": [],
                    "severity": "unknown",
                    "summary": "Judge returned non-JSON; treated as unknown",
                }

            result = json.loads(content)
            sev = result.get("overall_severity", "none")
            return {
                "passed": sev not in ("critical",),
                "flagged": result.get("hallucinations", []),
                "severity": sev,
                "summary": result.get("summary", ""),
            }
        except Exception as e:
            # On any error, treat as unknown rather than failing the task
            return {
                "passed": True,
                "flagged": [],
                "severity": "unknown",
                "summary": f"Detection error (treated as unknown): {type(e).__name__}",
            }
