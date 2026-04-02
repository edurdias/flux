from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Check 1: Fork bomb
# ---------------------------------------------------------------------------

_FORK_BOMB_RE = re.compile(
    r":\s*\(\s*\)\s*\{"  # :() {  — bash fork bomb function signature
    r"|while\s+true\s*[;{]"  # while true; / while true {
    r"|for\s*\(\s*;\s*;\s*\)",  # for (;;)
    re.IGNORECASE,
)


def check_fork_bomb(command: str) -> str | None:
    if _FORK_BOMB_RE.search(command):
        return "fork bomb detected"
    return None


# ---------------------------------------------------------------------------
# Check 2: Destructive commands
# ---------------------------------------------------------------------------

_DESTRUCTIVE_RE = re.compile(
    r"\brm\b[^;|&\n\r]*-[a-zA-Z]*[rf][a-zA-Z]*[rf][^;|&\n\r]*\s+/"  # rm -rf / or rm -fr /
    r"|\bmkfs\b"  # mkfs (any variant: mkfs.ext4, etc.)
    r"|\bdd\b[^;|&\n\r]*\bif=/dev/zero\b"  # dd if=/dev/zero
    r"|\bdd\b[^;|&\n\r]*\bof=/dev/[sh]d"  # dd of=/dev/sda or /dev/hda
    r"|>\s*/dev/[sh]d[a-z]?"  # redirect to block device
    r"|\bwipefs\b",  # wipefs
    re.IGNORECASE,
)


def check_destructive_commands(command: str) -> str | None:
    if _DESTRUCTIVE_RE.search(command):
        return "destructive command detected"
    return None


# ---------------------------------------------------------------------------
# Pipeline (expanded in subsequent tasks)
# ---------------------------------------------------------------------------

BASELINE_CHECKS = [
    check_fork_bomb,
    check_destructive_commands,
]


def run_security_checks(command: str) -> str | None:
    """Run all baseline security checks. Returns error message or None if safe."""
    for check in BASELINE_CHECKS:
        result = check(command)
        if result is not None:
            return result
    return None
