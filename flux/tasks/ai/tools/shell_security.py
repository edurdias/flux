from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Check 1: Fork bomb
# ---------------------------------------------------------------------------

_FORK_BOMB_RE = re.compile(
    r":\s*\(\s*\)\s*\{" r"|while\s+true\s*[;{]" r"|for\s*\(\s*;\s*;\s*\)",
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
    r"\brm\b[^;|&\n\r]*-[a-zA-Z]*[rf][a-zA-Z]*[rf][^;|&\n\r]*\s+/"
    r"|\bmkfs\b"
    r"|\bdd\b[^;|&\n\r]*\bif=/dev/zero\b"
    r"|\bdd\b[^;|&\n\r]*\bof=/dev/[sh]d"
    r"|>\s*/dev/[sh]d[a-z]?"
    r"|\bwipefs\b",
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
# Check 7: Unicode injection
# ---------------------------------------------------------------------------

_ALLOWED_CONTROL_CODEPOINTS = frozenset([0x09, 0x0A, 0x0D])


def check_unicode_injection(command: str) -> str | None:
    for ch in command:
        cp = ord(ch)
        if cp < 0x20 and cp not in _ALLOWED_CONTROL_CODEPOINTS:
            return "unicode injection detected: control character"
        if cp == 0x7F:
            return "unicode injection detected: DEL character"
        if cp > 0x7F and unicodedata.category(ch) == "Cf":
            return "unicode injection detected: invisible formatting character"
    return None


# ---------------------------------------------------------------------------
# Check 8: IFS and null-byte injection
# ---------------------------------------------------------------------------

_IFS_INJECTION_RE = re.compile(
    r"\bIFS\s*=" r"|\x00",
)


def check_ifs_injection(command: str) -> str | None:
    if _IFS_INJECTION_RE.search(command):
        return "IFS or null-byte injection detected"
    return None


# ---------------------------------------------------------------------------
# Check 9: Dangerous environment variable manipulation
# ---------------------------------------------------------------------------

_ENV_MANIP_RE = re.compile(
    r"\b(?:PATH|LD_PRELOAD|LD_LIBRARY_PATH|PYTHONPATH|PYTHONSTARTUP"
    r"|RUBYLIB|PERL5LIB|NODE_PATH|DYLD_INSERT_LIBRARIES)\s*=",
    re.IGNORECASE,
)


def check_env_manipulation(command: str) -> str | None:
    if _ENV_MANIP_RE.search(command):
        return "dangerous environment variable manipulation detected"
    return None


# ---------------------------------------------------------------------------
# Check 10: Privilege escalation
# ---------------------------------------------------------------------------

_PRIV_ESC_RE = re.compile(
    r"\bsudo\b"
    r"|\bsu\b"
    r"|\bchmod\b[^;|&\n\r]*\b777\b"
    r"|\bchmod\b[^;|&\n\r]*\+s\b"
    r"|\bchown\b[^;|&\n\r]*\broot\b"
    r"|\bpkexec\b"
    r"|\bnewgrp\b",
    re.IGNORECASE,
)


def check_privilege_escalation(command: str) -> str | None:
    if _PRIV_ESC_RE.search(command):
        return "privilege escalation detected"
    return None


# ---------------------------------------------------------------------------
# Check 11: Network exfiltration / reverse shell
# ---------------------------------------------------------------------------

_NET_EXFIL_RE = re.compile(
    r"\bnc\b[^;|&\n\r]*(?:-[a-zA-Z]*l\b|--listen\b)"
    r"|\bncat\b"
    r"|\bsocat\b[^;|&\n\r]*(?:TCP|UDP)-LISTEN"
    r"|bash\s+-i\s*>&?\s*/dev/tcp/"
    r"|sh\s+-i\s*>&?\s*/dev/tcp/"
    r"|/dev/tcp/",
    re.IGNORECASE,
)


def check_network_exfiltration(command: str) -> str | None:
    if _NET_EXFIL_RE.search(command):
        return "network exfiltration or reverse shell detected"
    return None


# ---------------------------------------------------------------------------
# Check 12: Crypto mining tools
# ---------------------------------------------------------------------------

_CRYPTO_MINING_RE = re.compile(
    r"\bxmrig\b" r"|\bminerd\b" r"|\bcpuminer\b" r"|stratum\+tcp://",
    re.IGNORECASE,
)


def check_crypto_mining(command: str) -> str | None:
    if _CRYPTO_MINING_RE.search(command):
        return "crypto mining tool detected"
    return None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

BASELINE_CHECKS = [
    check_fork_bomb,
    check_destructive_commands,
    check_system_control,
    check_protected_files,
    check_path_traversal,
    check_pipe_to_shell,
    check_unicode_injection,
    check_ifs_injection,
    check_env_manipulation,
    check_privilege_escalation,
    check_network_exfiltration,
    check_crypto_mining,
]


def run_security_checks(command: str) -> str | None:
    """Run all baseline security checks. Returns error message or None if safe."""
    for check in BASELINE_CHECKS:
        result = check(command)
        if result is not None:
            return result
    return None
