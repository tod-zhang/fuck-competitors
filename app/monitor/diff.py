"""Turn two text snapshots into display-ready diff rows (matches the drawer's diff UI).

Pure stdlib so it is unit-testable without dependencies.
Each row is {"t": "hunk"|"ctx"|"add"|"del", "x": <text>} — the same shape the frontend renders.
"""
from __future__ import annotations

import difflib


def make_hunks(old_text: str, new_text: str, context: int = 2) -> list[dict]:
    old = old_text.splitlines()
    new = new_text.splitlines()
    rows: list[dict] = []
    for line in difflib.unified_diff(old, new, n=context, lineterm=""):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("@@"):
            rows.append({"t": "hunk", "x": line.strip()})
        elif line.startswith("+"):
            rows.append({"t": "add", "x": line[1:]})
        elif line.startswith("-"):
            rows.append({"t": "del", "x": line[1:]})
        else:
            rows.append({"t": "ctx", "x": line[1:] if line.startswith(" ") else line})
    return rows
