"""
Shared utilities for core pipeline.

Common helpers used across multiple pipeline steps.
"""

import json
import re


def extract_json_from_response(text: str) -> dict | None:
    """
    Extract and parse JSON from LLM response.

    Handles common patterns:
    - JSON wrapped in markdown code blocks (```json ... ```)
    - Raw JSON objects
    - JSON embedded in text

    Args:
        text: Raw LLM response text

    Returns:
        Parsed dict or None if parsing fails
    """
    text = text.strip()

    # Remove markdown code blocks
    if "```json" in text:
        text = text.split("```json")[1]
    if "```" in text:
        text = text.split("```")[0]
    text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in text
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None
