"""Stable identity for a review comment, used to avoid repeating ourselves.

fingerprint = sha256(path + category + normalised code of the target lines)

Deliberately NOT included (ADR 0008):
- line numbers: they shift whenever code is added above;
- the LLM's wording: it varies between runs for the same problem.
So the same problem on the same code gets the same fingerprint on every push,
and is posted once.
"""

import hashlib


def normalize_code(code: str) -> str:
    # Collapse all whitespace: reindenting or reformatting doesn't make it a new problem.
    return "\n".join(" ".join(line.split()) for line in code.splitlines() if line.strip())


def fingerprint(path: str, category: str, code: str) -> str:
    material = f"{path}\x00{category}\x00{normalize_code(code)}"
    return hashlib.sha256(material.encode()).hexdigest()
