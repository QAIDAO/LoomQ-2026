#!/usr/bin/env python3
"""Small OpenAI-compatible transport helper for LoomQ L2 entrants.

This module deliberately contains no prompting strategy or scoring logic. Teams
may use it, replace it, or call the same environment-variable contract from any
language.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


REQUIRED_ENV = ("LOOMQ_LLM_BASE_URL", "LOOMQ_LLM_API_KEY", "LOOMQ_LLM_MODEL")


def _configuration() -> tuple[str, str, str, float, int]:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise RuntimeError("missing required LoomQ L2 environment variable(s): " + ", ".join(missing))
    try:
        timeout = float(os.environ.get("LOOMQ_LLM_TIMEOUT_SECONDS", "120"))
        max_output = int(os.environ.get("LOOMQ_LLM_MAX_OUTPUT_TOKENS", "4096"))
    except ValueError as exc:
        raise RuntimeError("invalid LoomQ L2 numeric environment variable") from exc
    if timeout <= 0 or max_output <= 0:
        raise RuntimeError("LoomQ L2 timeout and output-token limit must be positive")
    return (
        os.environ["LOOMQ_LLM_BASE_URL"].rstrip("/"),
        os.environ["LOOMQ_LLM_API_KEY"],
        os.environ["LOOMQ_LLM_MODEL"],
        timeout,
        max_output,
    )


def chat_completion(messages: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    """Create one non-streaming chat completion using the public L2 contract."""
    base_url, api_key, model, timeout, max_output = _configuration()
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": 0,
        "max_tokens": max_output,
    }
    if model == "deepseek-v4-flash":
        payload["thinking"] = {"type": "disabled"}
    payload.update(extra)
    request = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError("LoomQ L2 API returned HTTP %d" % exc.code) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("LoomQ L2 API is unreachable") from exc
