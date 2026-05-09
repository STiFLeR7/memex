import json
import logging
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
    """
    config = get_config()
    client = genai.Client(api_key=config.gemini_api_key)
    
    # Filter trivial messages early to save on LLM calls
    trivial_keywords = ["fix typo", "wip", "merge", "ignore", "formatting"]
    if any(kw in commit_message.lower() for kw in trivial_keywords):
        return []

    prompt = f"""
    Analyze the following git commit message and diff summary. 
    Extract zero or more architectural or technical decisions made in this commit.
    A decision is a deliberate choice about how the system is built, not just a bug fix or a description of code changes.
    
    Commit Message: {commit_message}
    Diff Summary: {diff_summary}
    
    If the commit is trivial (e.g., typos, formatting, WIP, merging), return an empty list.
    
    Return the decisions in a strict JSON format matching the schema.
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={
                'response_mime_type': 'application/json',
                'response_schema': DecisionsResponse,
            }
        )
        
        # The SDK returns the parsed response in .parsed if using response_schema
        # but let's handle the raw text to be safe across SDK versions
        data = json.loads(response.text)
        extracted_decisions = []
        
        for d in data.get("decisions", []):
            extracted_decisions.append(Decision(
                text=d["text"],
                rationale=d["rationale"],
                scope=d["scope"],
                source_commit=commit_sha
            ))
            
        return extracted_decisions

    except Exception as e:
        logger.error(f"Failed to extract decisions via Gemini: {e}")
        return []
