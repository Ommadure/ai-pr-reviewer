"""Unified diff → structured hunks with old/new line numbers.

Two entry points:
- parse_patch(): one file's `patch` field from GitHub's "list PR files" API
  (hunks only, no `diff --git` header).
- parse_unified_diff(): a whole `git diff` / `.patch` file (used by the CLI and evals).

Hunks are read by *count*: the header says how many old/new lines follow, so a
removed line whose text starts with "--" (a SQL comment, say) can't be mistaken
for a file header.
"""

import re

from app.review.models import DiffLine, FileDiff, FileStatus, Hunk

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
NO_NEWLINE_MARKER = "\\ No newline at end of file"


def parse_hunks(patch: str) -> tuple[Hunk, ...]:
    lines = [line.removesuffix("\r") for line in patch.split("\n")]
    hunks: list[Hunk] = []
    index = 0
    while index < len(lines):
        header = HUNK_HEADER.match(lines[index])
        index += 1
        if not header:
            continue  # text outside a hunk (e.g. trailing newline) is ignored
        old_start, old_len = int(header.group(1)), _count(header.group(2))
        new_start, new_len = int(header.group(3)), _count(header.group(4))
        old_remaining, new_remaining = old_len, new_len
        old_line, new_line = old_start, new_start
        body: list[DiffLine] = []

        while (old_remaining > 0 or new_remaining > 0) and index < len(lines):
            raw = lines[index]
            index += 1
            if raw.startswith("\\"):
                continue  # "\ No newline at end of file" describes the previous line
            marker, content = (raw[0], raw[1:]) if raw else (" ", "")
            if marker == "+":
                body.append(DiffLine("added", content, None, new_line))
                new_line += 1
                new_remaining -= 1
            elif marker == "-":
                body.append(DiffLine("removed", content, old_line, None))
                old_line += 1
                old_remaining -= 1
            elif marker == " ":
                body.append(DiffLine("context", content, old_line, new_line))
                old_line += 1
                new_line += 1
                old_remaining -= 1
                new_remaining -= 1
            else:
                index -= 1  # malformed/truncated hunk: stop and let the outer loop resync
                break
        # A marker right after the last counted line still belongs to this hunk.
        if index < len(lines) and lines[index].startswith(NO_NEWLINE_MARKER):
            index += 1
        hunks.append(
            Hunk(old_start, old_len, new_start, new_len, header.group(5).strip(), tuple(body))
        )
    return tuple(hunks)


def _count(value: str | None) -> int:
    # "@@ -3 +3 @@" means a one-line hunk: the count is optional and defaults to 1.
    return 1 if value is None else int(value)


def parse_patch(
    path: str,
    patch: str | None,
    *,
    status: FileStatus = "modified",
    previous_path: str | None = None,
) -> FileDiff:
    """Parse one file's hunks as returned by GitHub (`patch` may be missing)."""
    if patch is None:
        return FileDiff(path, status, previous_path=previous_path, patch_missing=True)
    return FileDiff(path, status, parse_hunks(patch), previous_path=previous_path)


def parse_unified_diff(text: str) -> list[FileDiff]:
    """Parse a full `git diff` into one FileDiff per file."""
    files: list[FileDiff] = []
    for block in _split_files(text):
        files.append(_parse_file_block(block))
    return files


def _split_files(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    return blocks


def _parse_file_block(block: list[str]) -> FileDiff:
    header = block[0].removesuffix("\r")
    old_path, new_path = _paths_from_diff_git(header)
    status: FileStatus = "modified"
    previous_path: str | None = None
    is_binary = False
    body_start = len(block)

    for index, raw in enumerate(block[1:], start=1):
        line = raw.removesuffix("\r")
        if line.startswith("@@"):
            body_start = index
            break
        if line.startswith("new file mode"):
            status = "added"
        elif line.startswith("deleted file mode"):
            status = "removed"
        elif line.startswith("rename from "):
            status, previous_path = "renamed", line.removeprefix("rename from ")
        elif line.startswith("rename to "):
            new_path = line.removeprefix("rename to ")
        elif line.startswith("copy from "):
            status, previous_path = "copied", line.removeprefix("copy from ")
        elif line.startswith("copy to "):
            new_path = line.removeprefix("copy to ")
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            is_binary = True
        elif line.startswith("--- ") and line != "--- /dev/null":
            old_path = _strip_prefix(line[4:])
        elif line.startswith("+++ ") and line != "+++ /dev/null":
            new_path = _strip_prefix(line[4:])

    path = old_path if status == "removed" else new_path
    if status == "renamed" and previous_path is None:
        previous_path = old_path
    hunks = parse_hunks("\n".join(block[body_start:])) if not is_binary else ()
    return FileDiff(path, status, hunks, previous_path=previous_path, is_binary=is_binary)


def _paths_from_diff_git(header: str) -> tuple[str, str]:
    # "diff --git a/src/x.py b/src/x.py". Paths with spaces are ambiguous here,
    # so the ---/+++ and rename lines (parsed afterwards) take precedence.
    rest = header.removeprefix("diff --git ")
    if " b/" in rest:
        old, new = rest.split(" b/", 1)
        return _strip_prefix(old), new
    return rest, rest


def _strip_prefix(path: str) -> str:
    path = path.split("\t", 1)[0]  # git may append a tab + timestamp
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path
