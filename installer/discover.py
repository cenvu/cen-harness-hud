"""Harness / environment discovery (read-only)."""

import platform
import shutil


def detect_harnesses() -> dict:
    return {
        "agy": shutil.which("agy"),
        "claude": shutil.which("claude"),
        "codex": shutil.which("codex"),
        "pi": shutil.which("pi"),
        "herdr": shutil.which("herdr"),
    }


def platform_info() -> dict:
    return {
        "platform": platform.system().lower(),  # darwin
        "architecture": platform.machine(),
        "python": platform.python_version(),
    }
