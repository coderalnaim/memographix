from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Cheap deterministic token estimate used for local budgeting."""
    return max(1, len(text) // 4)


def trim_to_budget(text: str, token_budget: int) -> str:
    if token_budget <= 0:
        return ""
    limit = token_budget * 4
    if len(text) <= limit:
        return text
    marker = "\n... [trimmed to token budget]"
    return text[: max(0, limit - len(marker))].rstrip() + marker

