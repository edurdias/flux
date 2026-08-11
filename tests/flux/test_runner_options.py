"""runner_options: a workflow may tighten its container, never loosen it.

The options travel with the workflow like requests/affinity/routing, so they
are author-controlled — anyone with :register sets them. Tightening is
therefore guaranteed by construction rather than by validation: the runner
takes the stricter of its configured value and the requested one, so a larger
number or a wider setting cannot widen anything.
"""

from __future__ import annotations

import pytest

from flux.runners.options import (
    KNOWN_RUNNER_OPTIONS,
    parse_memory,
    tighten_options,
    validate_runner_options,
)


class TestValidation:
    """Registration-time: reject what is not a known tightening knob, so a
    typo fails loudly instead of being silently ignored at dispatch."""

    def test_known_keys_pass(self):
        validate_runner_options({"cpus": 0.5, "memory": "256m", "timeout": 30})

    def test_unknown_key_is_rejected(self):
        with pytest.raises(ValueError, match="unknown runner option"):
            validate_runner_options({"privileged": True})

    def test_loosening_values_are_rejected(self):
        with pytest.raises(ValueError, match="network"):
            validate_runner_options({"network": "host"})
        with pytest.raises(ValueError, match="read_only"):
            validate_runner_options({"read_only": False})

    def test_dropping_the_network_is_allowed(self):
        validate_runner_options({"network": "none", "read_only": True})

    def test_non_positive_numbers_are_rejected(self):
        with pytest.raises(ValueError, match="cpus"):
            validate_runner_options({"cpus": 0})
        with pytest.raises(ValueError, match="timeout"):
            validate_runner_options({"timeout": -1})

    def test_every_known_key_is_documented_as_tightening(self):
        assert set(KNOWN_RUNNER_OPTIONS) == {
            "cpus",
            "memory",
            "timeout",
            "network",
            "read_only",
        }


class TestParseMemory:
    @pytest.mark.parametrize(
        "text,expected",
        [("512m", 512 * 1024**2), ("1g", 1024**3), ("1024k", 1024 * 1024), ("900", 900)],
    )
    def test_suffixes(self, text, expected):
        assert parse_memory(text) == expected

    def test_rejects_nonsense(self):
        with pytest.raises(ValueError):
            parse_memory("lots")


class TestTightenOnly:
    """The heart of it: a request can only ever narrow."""

    def test_smaller_numbers_win(self):
        out = tighten_options({"cpus": 2.0, "memory": "1g"}, {"cpus": 0.5, "memory": "256m"})
        assert out["cpus"] == 0.5
        assert out["memory"] == "256m"

    def test_larger_numbers_are_ignored(self):
        out = tighten_options({"cpus": 1.0, "memory": "512m"}, {"cpus": 8.0, "memory": "16g"})
        assert out["cpus"] == 1.0
        assert out["memory"] == "512m"

    def test_unset_configured_value_accepts_the_request(self):
        out = tighten_options({}, {"cpus": 1.5, "memory": "256m"})
        assert out["cpus"] == 1.5
        assert out["memory"] == "256m"

    def test_network_can_only_be_dropped(self):
        assert tighten_options({"network": "bridge"}, {"network": "none"})["network"] == "none"
        # A wider request cannot restore what config already dropped.
        assert tighten_options({"network": "none"}, {"network": "bridge"})["network"] == "none"

    def test_read_only_is_sticky_once_set(self):
        assert tighten_options({}, {"read_only": True})["read_only"] is True
        assert tighten_options({"read_only": True}, {"read_only": False})["read_only"] is True


class TestRunnerAcceptance:
    def test_docker_applies_the_options(self):
        from flux.runners.docker import DockerRunner

        runner = DockerRunner(image="flux:test", cpus=4.0, memory="2g")
        command = runner._build_command("c1", options={"cpus": 0.5, "memory": "256m"})
        assert ["--cpus", "0.5"] == command[command.index("--cpus") : command.index("--cpus") + 2]
        assert ["--memory", "256m"] == command[
            command.index("--memory") : command.index("--memory") + 2
        ]

    def test_docker_ignores_a_widening_request(self):
        from flux.runners.docker import DockerRunner

        runner = DockerRunner(image="flux:test", cpus=1.0, memory="512m")
        command = runner._build_command("c1", options={"cpus": 8.0, "memory": "16g"})
        assert ["--cpus", "1.0"] == command[command.index("--cpus") : command.index("--cpus") + 2]
        assert ["--memory", "512m"] == command[
            command.index("--memory") : command.index("--memory") + 2
        ]

    def test_airgapped_profile_still_wins(self):
        """The locked profile is emitted last, so no option can undo it."""
        from unittest.mock import MagicMock, patch

        from flux.runners.docker import AirgappedDockerRunner

        with (
            patch("flux.runners.docker.shutil.which", return_value="/usr/bin/docker"),
            patch("flux.runners.docker.subprocess.run") as run,
        ):
            run.return_value = MagicMock(returncode=0, stdout="27", stderr="")
            runner = AirgappedDockerRunner(image="flux:test")
        command = runner._build_command("c1", options={"network": "none", "cpus": 0.5})
        assert "--network=none" in command
        assert command.index("--network=none") > command.index("--cpus")

    def test_subprocess_runner_accepts_no_options(self):
        from flux.runners.subprocess_runner import SubprocessRunner

        assert SubprocessRunner().accepts_options is False


class TestRegistration:
    """Options travel like requests/affinity: extracted from source by AST, so
    an invalid value must fail registration, not dispatch."""

    def _parse(self, source: str):
        from flux.catalogs import WorkflowCatalog

        return WorkflowCatalog.create().parse_static(source.encode())

    def test_options_reach_workflow_metadata(self):
        infos = self._parse(
            "from flux import workflow, ExecutionContext\n"
            '@workflow.with_options(runner="docker", runner_options={"cpus": 0.5})\n'
            "async def w(ctx: ExecutionContext):\n"
            "    return 1\n",
        )
        assert infos[0].metadata["runner_options"] == {"cpus": 0.5}

    def test_unknown_option_fails_registration(self):
        # The catalog surfaces a parse-time rejection as SyntaxError, keeping
        # the reason in the message.
        with pytest.raises((ValueError, SyntaxError), match="unknown runner option"):
            self._parse(
                "from flux import workflow, ExecutionContext\n"
                '@workflow.with_options(runner="docker", runner_options={"privileged": True})\n'
                "async def w(ctx: ExecutionContext):\n"
                "    return 1\n",
            )

    def test_decorator_validates_at_definition_time(self):
        from flux import workflow

        with pytest.raises(ValueError, match="unknown runner option"):

            @workflow.with_options(runner="docker", runner_options={"privileged": True})
            async def w(ctx):
                return 1
