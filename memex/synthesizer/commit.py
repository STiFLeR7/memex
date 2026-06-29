import json
import logging
import asyncio
from typing import List
from pydantic import BaseModel
from google import genai
from memex.config import get_config
from memex.graph.schema import Decision

logger = logging.getLogger(__name__)

class DecisionSchema(BaseModel):
    text: str
    rationale: str
    scope: str  # local, module, project

class DecisionsResponse(BaseModel):
    decisions: List[DecisionSchema]

async def extract_decisions(
    commit_message: str,
    diff_summary: str,
    commit_sha: str,
) -> List[Decision]:
    """
    Uses Gemini Flash to extract zero or more architectural decisions from a commit.
    Includes rate limit retries and trivial commit filtering.
    """
    # Guard against empty/whitespace messages
    if not commit_message or not commit_message.strip():
        return []

    # Trivial commit filter (regex-like logic)
    trivial_prefixes = ("wip", "fix", "typo", "merge", "bump", "fmt", "format", "lint", "style")
    msg_lower = commit_message.lower().strip()
    if any(msg_lower.startswith(pref) for pref in trivial_prefixes) or len(msg_lower.split()) < 2:
        logger.debug("Skipping trivial commit: %s", commit_message)
        return []

    config = get_config()
    client = genai.Client(api_key=config.gemini_api_key)
    
    prompt = f"""
    Analyze the following git commit message and diff summary. 
    Extract zero or more architectural or technical decisions made in this commit.
    A decision is a deliberate choice about how the system is built, not just a bug fix or a description of code changes.
    
    Commit Message: {commit_message}
    Diff Summary: {diff_summary}
    
    If the commit is trivial (e.g., typos, formatting, WIP, merging), return an empty list.
    
    Return the decisions in a strict JSON format matching the schema.
    """

    # Retry logic with exponential backoff
    for attempt in range(3):
        try:
            # google-genai's generate_content is synchronous. Wrap it in
            # asyncio.to_thread so the watcher event loop isn't blocked for
            # the full LLM round-trip (typically 1-5s on Gemini Flash).
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=config.gemini_model,
                contents=prompt,
                config={
                    'response_mime_type': 'application/json',
                    'response_schema': DecisionsResponse,
                },
            )
            
            data = json.loads(response.text)
            extracted_decisions = []
            
            for d in data.get("decisions", []):
                # v0.3.0 defaults (Phase 8 — Hallucination Mitigation):
                #   validated=False  — watcher-synthesised, must be approved via `memex review`
                #   base_confidence — config-driven (Signal Pillar A): the
                #     synthesiser routes through the same initial-confidence
                #     resolver as agent writes instead of hardcoding 0.6, so a
                #     repo can tune how much it trusts unreviewed synthesis.
                #     harness=None resolves the `default` harness (default 0.6).
                #   source="watcher" — distinguishes from agent-recorded decisions
                # ``last_reinforced_at`` is set to ``created_at`` by the writer so the
                # computed_confidence helper has an anchor on freshly-synthesised nodes.
                extracted_decisions.append(Decision(
                    text=d["text"],
                    rationale=d["rationale"],
                    scope=d["scope"],
                    source_commit=commit_sha,
                    source="watcher",
                    validated=False,
                    base_confidence=config.initial_confidence_for(None),
                ))
                
            return extracted_decisions

        except Exception as e:
            # Check for rate limit or other retryable errors
            err_str = str(e).lower()
            if "429" in err_str or "rate limit" in err_str:
                wait_time = (2 ** attempt) + 1
                logger.warning("Gemini rate limit hit. Retrying in %ds...", wait_time)
                await asyncio.sleep(wait_time)
                continue
            
            logger.error("Failed to extract decisions via Gemini", exc_info=True)
            return []

    logger.error("Failed to extract decisions after 3 attempts due to rate limits.")
    return []
