from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from flux.task import task

from flux.tasks.ai.tools.system_tools import truncate_output

if TYPE_CHECKING:
    from flux.tasks.ai.tools.system_tools import SystemToolsConfig

logger = logging.getLogger("flux.tools.shell")


def build_shell_tools(config: SystemToolsConfig) -> list:
    compiled_blocklist = [re.compile(p) for p in config.blocklist]

    @task.with_options(timeout=config.timeout)
    async def shell(command: str, stream: bool = False) -> dict:
        """Execute a shell command in the workspace directory."""
        for pattern in compiled_blocklist:
            if pattern.search(command):
                return {"status": "error", "error": "command blocked by security policy"}

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(config.workspace),
        )

        assert proc.stdout is not None
        assert proc.stderr is not None

        try:
            if stream:
                from flux.tasks.progress import progress

                stdout_chunks: list[str] = []
                stderr_chunks: list[str] = []

                async def _read_stderr():
                    assert proc.stderr is not None
                    while True:
                        chunk = await proc.stderr.read(4096)
                        if not chunk:
                            break
                        stderr_chunks.append(chunk.decode(errors="replace"))

                stderr_task = asyncio.create_task(_read_stderr())

                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    text = chunk.decode(errors="replace")
                    stdout_chunks.append(text)
                    await progress({"token": text})

                await stderr_task
                stdout_full = "".join(stdout_chunks)
                stderr_full = "".join(stderr_chunks)
                await proc.wait()
            else:
                stdout_bytes, stderr_bytes = await proc.communicate()
                stdout_full = stdout_bytes.decode(errors="replace")
                stderr_full = stderr_bytes.decode(errors="replace")
        except (asyncio.CancelledError, Exception):
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (TimeoutError, ProcessLookupError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
            raise

        stdout, stdout_truncated = truncate_output(stdout_full, config.max_output_chars)
        stderr, stderr_truncated = truncate_output(stderr_full, config.max_output_chars)
        truncated = stdout_truncated or stderr_truncated

        result = {
            "status": "ok",
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        if truncated:
            result["truncated"] = True
            result["total_chars"] = len(stdout_full) + len(stderr_full)
        return result

    return [shell]
