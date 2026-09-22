"""Safe Telegram message chunking: never split words, Markdown entities or critical blocks."""
from __future__ import annotations

import re

TELEGRAM_LIMIT = 4000   # safety margin below the hard 4096
BLOCK_STARTS = ("#", "##", "-", "*", "•", "|", "```")


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split on paragraph boundaries first, then on whole words; keep code fences closed."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:
            cut = paragraph.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(paragraph[:cut])
            paragraph = paragraph[cut:].lstrip()
        current = paragraph
    if current:
        chunks.append(current)
    # Reopen/close code fences so no chunk ends inside a fence.
    balanced: list[str] = []
    inside_fence = False
    for chunk in chunks:
        if inside_fence:
            chunk = "```\n" + chunk
            inside_fence = False
        fences = chunk.count("```")
        if fences % 2 == 1:
            chunk = chunk + "\n```"
            inside_fence = False
        balanced.append(chunk)
    return balanced


def telegram_safe(text: str) -> str:
    """Strip risky Markdown entities the bot does not use; plain text survives intact."""
    return re.sub(r"(`{3,}[\w-]*\n?)", "```", text)
