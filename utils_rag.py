# utils_rag.py

import json

def extract_llm_text(llm_out: dict):
    """Robust response extractor supporting GPT4All, LM Studio, Ollama, OpenAI, and plain text formats."""

    # ---- 1) OpenAI Format (chat.completions) ----
    if isinstance(llm_out, dict) and "choices" in llm_out and llm_out["choices"]:
        choice = llm_out["choices"][0]

        # Case: OpenAI format
        if "message" in choice and "content" in choice["message"]:
            return choice["message"]["content"]

        # Case: GPT4All returning text field
        if "text" in choice and choice["text"]:
            return choice["text"]

        # Case: Delta streaming style format
        if "delta" in choice and "content" in choice["delta"]:
            return choice["delta"]["content"]

    # ---- 2) GPT4All / LM Studio direct "content" ----
    if "content" in llm_out and isinstance(llm_out["content"], str):
        return llm_out["content"]

    # ---- 3) GPT4All alt property "response" ----
    if "response" in llm_out and isinstance(llm_out["response"], str):
        return llm_out["response"]

    # ---- 4) Plain String ----
    if isinstance(llm_out, str):
        return llm_out

    # ---- 5) Fallback to readable JSON ----
    return json.dumps(llm_out, indent=2)
