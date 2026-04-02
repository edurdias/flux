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
# Check 3: System control commands
# ---------------------------------------------------------------------------

_SYSTEM_CONTROL_RE = re.compile(
    r"\bshutdown\b"
    r"|\breboot\b"
    r"|\bhalt\b"
    r"|\binit\s+[06]\b"
    r"|\bsystemctl\b[^;|&\n\r]*\b(?:poweroff|halt|reboot)\b",
    re.IGNORECASE,
)


def check_system_control(command: str) -> str | None:
    if _SYSTEM_CONTROL_RE.search(command):
        return "system control command detected"
    return None


# ---------------------------------------------------------------------------
# Check 4: Protected files
# ---------------------------------------------------------------------------

_PROTECTED_PATHS = (
    r"~?/?\.env\b"
    r"|~?/?\.ssh(?:/|$)"
    r"|~?/?\.gitconfig\b"
    r"|~?/?\.bashrc\b"
    r"|~?/?\.bash_profile\b"
    r"|~?/?\.profile\b"
    r"|~?/?\.mcp\.json\b"
    r"|~?/?\.ssh/authorized_keys\b"
    r"|~?/?\.ssh/id_rsa\b"
    r"|~?/?\.ssh/id_ed25519\b"
    r"|credentials"
)

_PROTECTED_FILES_RE = re.compile(
    r"(?:>>?\s*|2>>?\s*)(?:" + _PROTECTED_PATHS + r")"
    r"|\b(?:rm|cp|mv|chmod|chown|tee)\b[^;|&\n\r]*(?:" + _PROTECTED_PATHS + r")",
    re.IGNORECASE,
)


def check_protected_files(command: str) -> str | None:
    if _PROTECTED_FILES_RE.search(command):
        return "write to protected file detected"
    return None


# ---------------------------------------------------------------------------
# Check 5: Path traversal
# ---------------------------------------------------------------------------

_URL_DOT_RE = re.compile(r"%2e", re.IGNORECASE)
_URL_SLASH_RE = re.compile(r"%2f", re.IGNORECASE)


def check_path_traversal(command: str) -> str | None:
    normalized = unicodedata.normalize("NFC", command)
    decoded = _URL_DOT_RE.sub(".", normalized)
    decoded = _URL_SLASH_RE.sub("/", decoded)
    if re.search(r"\.\.[/\\]", decoded) or re.search(r"[/\\]\.\.", decoded):
        return "path traversal detected"
    return None


# ---------------------------------------------------------------------------
# Check 6: Pipe to shell (download-and-execute)
# ---------------------------------------------------------------------------

_PIPE_TO_SHELL_RE = re.compile(
    r"\b(?:curl|wget|fetch|http(?:ie)?)\b[^;|&\n\r]*"
    r"\|[^;|&\n\r]*"
    r"\b(?:bash|sh|zsh|dash|ash|python\d*|perl|ruby|node)\b",
    re.IGNORECASE,
)


def check_pipe_to_shell(command: str) -> str | None:
    if _PIPE_TO_SHELL_RE.search(command):
        return "download and execute detected"
    return None


# ---------------------------------------------------------------------------
# Pipeline (expanded in subsequent tasks)
# ---------------------------------------------------------------------------

BASELINE_CHECKS = [
    check_fork_bomb,
    check_destructive_commands,
    check_system_control,
    check_protected_files,
    check_path_traversal,
    check_pipe_to_shell,
]


def run_security_checks(command: str) -> str | None:
    """Run all baseline security checks. Returns error message or None if safe."""
    for check in BASELINE_CHECKS:
        result = check(command)
        if result is not None:
            return result
    return None
