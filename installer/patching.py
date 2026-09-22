"""Config surgery: JSON patch, fail-closed TOML insertion, symlinks.

TOML policy (high-risk area):
- parse-validate the WHOLE file with tomllib before and after any mutation
- only INSERT CEN-owned keys; never rewrite or delete foreign lines
- never regenerate a foreign TOML file from a template
- ambiguous ownership → FAIL CLOSED (caller raises ConflictError/ComponentError)
"""

import json
import os
import re

try:
    import tomllib
except ImportError: # pragma: no cover — Python <3.11
    tomllib = None

from .paths import ComponentError

SECTION_RE = re.compile(r"^\s*\[\s*([A-Za-z0-9_.\-]+)\s*\]\s*(#.*)?$")


def require_tomllib():
    if tomllib is None:
        raise ComponentError(
            "tomllib unavailable (Python >= 3.11 required for TOML targets); "
            "refusing to patch TOML blind (fail-closed)"
        )


# ── JSON ──────────────────────────────────────────────────────────────────────

def load_json(path: str):
    with open(path, "r") as f:
        return json.load(f) # ValueError/JSONDecodeError → caller fails closed


def apply_json_updates(data: dict, updates: dict) -> dict:
    """Top-level-key updates preserving all unrelated fields and ordering."""
    out = dict(data)
    for k, v in updates.items():
        out[k] = v
    return out


def dump_json(data) -> bytes:
    return (json.dumps(data, indent=2) + "\n").encode()


# ── TOML ──────────────────────────────────────────────────────────────────────

def toml_value(text: str):
    """Parse-validate whole document; raises on malformed TOML."""
    require_tomllib()
    return tomllib.loads(text)


def _norm_table(name: str) -> tuple:
    return tuple(p.strip() for p in name.split("."))


def find_section_span(lines: list, table: tuple):
    """Return (start_line, end_line_exclusive) of a [t.sub] section header,
    or (None, None) when absent. Comments inside headers tolerated."""
    target = ".".join(table)
    start = None
    end = len(lines)
    for i, ln in enumerate(lines):
        m = SECTION_RE.match(ln)
        if not m:
            continue
        cur = ".".join(_norm_table(m.group(1)))
        if start is None and cur == target:
            start = i
            continue
        if start is not None:
            end = i
            break
    if start is None:
        return None, None
    return start, end


KEY_RE_TMPL = r"^\s*(\"?{k}\"?)\s*="


def classify_toml_key(text: str, table: tuple, key: str, desired_value):
    """Classify without mutating: absent | equal | foreign | malformed.

    `desired_value` is the PARSED python value considered CEN-owned.
    """
    parsed = toml_value(text) # may raise → caller maps to malformed/fail
    node = parsed
    for t in table:
        if isinstance(node, dict) and t in node:
            node = node[t]
        else:
            node = None
            break
    if not isinstance(node, dict) or key not in node:
        return "absent"
    return "equal" if node[key] == desired_value else "foreign"


def insert_toml_key(text: str, table: tuple, key: str, literal_lines: list) -> str:
    """Insert `key` into [table] section of text WITHOUT touching any
    existing line. Section created at EOF when absent. Returns new text.
    Caller must have classified the key as 'absent'."""
    lines = text.splitlines()
    block = ["{k} = [".format(k=key)] + literal_lines + ["]"]
    start, end = find_section_span(lines, table)
    if start is None:
        new_lines = lines + ["", "[" + ".".join(table) + "]"] + block
    else:
 # back up over trailing blank lines/comments belonging to the gap,
 # but never modify them — insert immediately before next section
        insert_at = end
        new_lines = lines[:insert_at] + block + lines[insert_at:]
    result = "\n".join(new_lines)
    if text.endswith("\n"):
        result += "\n"
    return result


def insert_toml_scalar(text: str, table: tuple, key: str, literal: str) -> str:
    """Insert one scalar key without rewriting unrelated TOML bytes."""
    lines = text.splitlines()
    block = [f"{key} = {literal}"]
    start, end = find_section_span(lines, table)
    if start is None:
        new_lines = lines + ["", "[" + ".".join(table) + "]"] + block
    else:
        new_lines = lines[:end] + block + lines[end:]
    result = "\n".join(new_lines)
    if text.endswith("\n"):
        result += "\n"
    return result


def _find_array_block_end(lines: list, start: int) -> int:
    """Return the index of the line closing the top-level array whose
    opener is line `start` (`key = [`).

    Bounded bracket-depth scanner, string-aware (basic strings with
    backslash escapes) and comment-aware (`#` outside strings), so a
    bracket inside a quoted string can never terminate the block and an
    INNER closing bracket (nested row array) cannot truncate it. Anything
    it does not understand raises — callers fail closed rather than
    rewrite blindly.
    """
    depth = 0
    for j in range(start, len(lines)):
        in_str = False
        i = 0
        line = lines[j]
        while i < len(line):
            ch = line[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "#":
                    break # comment: ignore rest of line
                elif ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth < 0:
                        raise ValueError(
                            "unbalanced brackets in owned block")
                    if depth == 0:
                        rest = line[i + 1:].strip()
                        if rest and not rest.startswith("#"):
                            raise ValueError(
                                "unexpected trailing content after owned "
                                "block close")
                        return j
            i += 1
        if in_str:
            raise ValueError("unterminated string in owned block")
    raise ValueError("unterminated owned key block")


def replace_toml_key(text: str, table: tuple, key: str,
                     literal_lines: list) -> str:
    """Replace ONLY an existing `key = ...` array inside [table].

    Handles both the CEN-owned multiline array-block layout this product
    writes (`key = [` ... outer-close) and a single-line owned array
    (`key = [...]` on one line, e.g. hand-edited or upstream-normalized
    TOML). Nested arrays are handled by the bounded depth scanner so an
    inner closing bracket can never terminate the replacement early.
    Anything else (missing block, unbalanced input) raises — callers must
    fail closed. The caller must already have verified the current PARSED
    value; this helper is purely mechanical line replacement and touches
    no other byte of the file.
    """
    lines = text.splitlines()
    sec_start, sec_end = find_section_span(lines, table)
    if sec_start is None:
        raise ValueError("target table not found: " + ".".join(table))
    opener = "{k} = [".format(k=key)
    key_re = re.compile(r"^\s*\"?%s\"?\s*=" % re.escape(key))
    key_start = None
    single_line = False
    for i in range(sec_start, min(sec_end, len(lines))):
        stripped = lines[i].strip()
        if lines[i].rstrip() == opener:
            key_start = i
            single_line = False
            break
        if key_re.match(lines[i]):
            # Candidate single-line owned array: must contain an array
            # opener. The caller verified the parsed value is owned, so
            # this line is the owned key. Replace just this line.
            if "[" in lines[i]:
                key_start = i
                single_line = True
                break
    if key_start is None:
        raise ValueError("owned key block not found: " + key)
    block = [opener] + list(literal_lines) + ["]"]
    if single_line:
        new_lines = lines[:key_start] + block + lines[key_start + 1:]
    else:
        key_end = _find_array_block_end(lines, key_start)
        new_lines = lines[:key_start] + block + lines[key_end + 1:]
    result = "\n".join(new_lines)
    if text.endswith("\n"):
        result += "\n"
    return result


# ── Symlinks ──────────────────────────────────────────────────────────────────

def classify_symlink(link: str, desired_target: str, cen_roots: list) -> str:
    """absent | equal | cen_retargetable | conflict"""
    if not os.path.lexists(link):
        return "absent"
    if os.path.islink(link):
        cur = os.readlink(link)
        if cur == desired_target:
            return "equal"
        real = os.path.realpath(link)
        for root in cen_roots:
            if real == os.path.realpath(root) or real.startswith(
                os.path.realpath(root) + os.sep
            ):
                return "cen_retargetable"
        return "conflict"
 # regular file/dir occupying the link slot
    return "conflict"
