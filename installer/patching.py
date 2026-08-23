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
