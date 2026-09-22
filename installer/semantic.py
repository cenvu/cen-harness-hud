"""Schema-v2 semantic ownership: records + uninstall inverses.

A semantic record describes exactly ONE CEN-owned config surface so a
successful install's final uninstall can remove/restore ONLY that surface
when it still equals the recorded CEN after-state — instead of restoring
whole-file bytes that may contain inherited older CEN state (F-2/F-3).

Record shape (all JSON-serializable, no account/credential data):
  path: absolute target path (converted to ~/ form at manifest build)
  format: "json" | "toml"
  op: "JSON_KEY" | "JSON_LIST_ENTRY" | "TOML_KEY" | "CODEX_SESSION_HOOK"
  key: dotted JSON path | list-match id | TOML key name | CEN hook command
  table: TOML table tuple (TOML_KEY only)
  before: {"state": "absent"} | {"state": "value", "value": <parsed>}
  after: parsed CEN value written (or True for hook presence)
  basis: "insert" | "adopt" | "migrate"
  created_file: file did not exist before this install attempt
  containers: [{"path": dotted, "keys": [CEN-written keys]}] (JSON, for
    pruning CEN-created empty containers; TOML headers prune automatically)

Same-attempt install rollback keeps using full private backups (journal);
this module is ONLY the final-uninstall inverse path.
"""

from __future__ import annotations

import json
import os

from . import patching


ABSENT = {"state": "absent"}


def value_before(value):
    return {"state": "value", "value": value}


def rec(path, format, op, key, before, after, basis,
        table=None, created_file=False, containers=None):
    return {
        "path": path,
        "format": format,
        "op": op,
        "key": key,
        "table": list(table) if table else None,
        "before": before,
        "after": after,
        "basis": basis,
        "created_file": bool(created_file),
        "containers": containers or [],
    }


def _read_text(path):
    with open(path, "r") as f:
        return f.read()


def _write_text(path, text, mode):
    from .paths import atomic_write

    atomic_write(path, text.encode(), mode)


def _read_json(path):
    with open(path, "r") as f:
        data = json.load(f)
    return data


def _write_json(path, data, mode):
    from .paths import atomic_write

    atomic_write(path, patching.dump_json(data), mode)


def _mode(path):
    import stat as st

    return st.S_IMODE(os.stat(path).st_mode)


def current_value(record):
    """Read the live semantic value. Returns (state, value|None).

    state: "missing" (surface absent/unreadable-structure) | "value" |
    "unparsable" (do not touch).
    """
    path = record["path"]
    op = record["op"]
    try:
        if record["format"] == "json":
            data = _read_json(path)
            if op == "JSON_KEY":
                found, val = patching.json_get_path(data, record["key"])
                return ("value", val) if found else ("missing", None)
            if op == "JSON_LIST_ENTRY":
                if not isinstance(data, list):
                    return ("missing", None)
                want = record["key"]
                for entry in data:
                    if (isinstance(entry, dict)
                            and entry.get("plugin_id") == want):
                        return ("value", entry)
                return ("missing", None)
            if op == "CODEX_SESSION_HOOK":
                if not isinstance(data, dict):
                    return ("missing", None)
                present = patching.codex_hook_entry_present(
                    data, record["key"])
                return ("value", True) if present else ("missing", None)
            return ("unparsable", None)
        # toml
        text = _read_text(path)
        found, val = patching.toml_get_key(
            text, tuple(record["table"]), record["key"])
        return ("value", val) if found else ("missing", None)
    except (OSError, ValueError):
        return ("unparsable", None)
    except Exception:
        return ("unparsable", None)


def _before_satisfied(record):
    """True when the live surface already equals the user baseline."""
    state, val = current_value(record)
    before = record["before"]
    if before.get("state") == "absent":
        return state == "missing"
    return state == "value" and val == before.get("value")


def _at_after(record):
    state, val = current_value(record)
    if state != "value":
        return False
    if record["op"] == "CODEX_SESSION_HOOK":
        return True
    return val == record["after"]


def apply_inverse(record):
    """Remove/restore one owned surface known to equal its after-state.

    Returns True when a mutation was written. Raises ComponentError on
    surgical failure (caller treats as drift, never overwrites blindly).
    """
    from .paths import ComponentError

    path = record["path"]
    mode = _mode(path)
    op = record["op"]
    try:
        if record["format"] == "json":
            data = _read_json(path)
            before = record["before"]
            if op == "JSON_KEY":
                if before.get("state") == "absent":
                    patching.json_delete_path(data, record["key"])
                else:
                    patching.json_set_path(
                        data, record["key"], before["value"])
                patching.json_prune_containers(data, record["containers"])
            elif op == "JSON_LIST_ENTRY":
                want = record["key"]
                patching.json_remove_list_entry(
                    data if isinstance(data, list) else [],
                    lambda e: (isinstance(e, dict)
                               and e.get("plugin_id") == want),
                )
                if not isinstance(data, list):
                    raise ComponentError("registry root is not a list")
            elif op == "CODEX_SESSION_HOOK":
                patching.codex_remove_hook_entry(data, record["key"])
                patching.json_prune_containers(data, record["containers"])
            else:
                raise ComponentError(f"unknown json op {op}")
            _write_json(path, data, mode)
            return True
        # toml: inverses are always removals (befores are ABSENT by design)
        text = _read_text(path)
        new_text = patching.toml_remove_key(
            text, tuple(record["table"]), record["key"])
        patching.toml_value(new_text) # parse-validate before committing
        _write_text(path, new_text, mode)
        return True
    except Exception as e:
        from .paths import ComponentError as CE

        if isinstance(e, CE):
            raise
        raise CE(f"semantic inverse failed for {record['key']}: {e}")


def classify_record(record):
    """Return 'resolve' | 'clean' | 'drift' without mutating."""
    if _at_after(record):
        return "resolve"
    if _before_satisfied(record):
        return "clean"
    return "drift"


def file_has_user_content(path, created_containers):
    """True when a CEN-created file still holds non-CEN content.

    JSON: any top-level key outside pruned CEN containers. TOML: any parsed
    key at all (owned keys are removed first by the caller; remaining parsed
    content is by definition user content or unmanaged structure).
    Unparsable files count as user content (never delete blindly).
    """
    try:
        if path.endswith(".json"):
            data = _read_json(path)
            patching.json_prune_containers(data, created_containers)
            if isinstance(data, dict):
                return bool(data)
            if isinstance(data, list):
                return bool(data)
            return True
        text = _read_text(path)
        parsed = patching.toml_value(text)
        return bool(parsed)
    except Exception:
        return True
