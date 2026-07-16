"""Tests for the docker-airgapped runner and the generic execution timeout.

Command-profile and validation tests run everywhere (no docker needed).
The timeout tests exercise a real subprocess child, same as the subprocess
runner suite. Container-level integration (network truly absent, read-only
rootfs) is gated on ``FLUX_TEST_DOCKER_IMAGE`` like the docker runner suite.
"""

from __future__ import annotations

import base64
import os
import textwrap
from unittest.mock import MagicMock, patch

import pytest

from flux.errors import ExecutionTimedOut
from flux.runners import KNOWN_RUNNERS, create_runners
from flux.runners.docker import AirgappedDockerRunner, DockerRunner
from flux.runners.subprocess_runner import SubprocessRunner

DOCKER_TEST_IMAGE = os.environ.get("FLUX_TEST_DOCKER_IMAGE")


def make_runner(**kwargs) -> AirgappedDockerRunner:
    with patch.object(DockerRunner, "_verify_docker_available"):
        return AirgappedDockerRunner(image=kwargs.pop("image", "flux:test"), **kwargs)


class TestLockedProfile:
    def test_every_locked_flag_present(self):
        command = make_runner()._build_command("c")
        joined = " ".join(command)

        assert "--network=none" in command
        assert "--read-only" in command
        assert "--cap-drop=ALL" in command
        assert "--security-opt=no-new-privileges" in command
        assert "--tmpfs /tmp:rw,size=64m" in joined
        assert "--pids-limit 256" in joined
        assert "--memory 512m" in joined
        assert "--cpus 1.0" in joined
        assert command[-4:] == ["flux:test", "python", "-m", "flux.runners.child"]

    def test_locked_flags_come_after_extra_args(self):
        """Docker's last-wins parsing must favor the profile: everything an
        operator adds sits before every locked flag."""
        runner = make_runner(extra_args=["--env", "TZ=UTC", "--user", "1000"])
        command = runner._build_command("c")
        env_at = command.index("--env")
        assert env_at < command.index("--network=none")
        assert env_at < command.index("--read-only")
        assert env_at < command.index("--memory")

    def test_profile_limits_not_duplicated_by_base_resource_args(self):
        command = make_runner()._build_command("c")
        assert command.count("--memory") == 1
        assert command.count("--cpus") == 1
        assert "--network" not in command  # only the fused --network=none form

    def test_configured_limits_flow_into_profile(self):
        runner = make_runner(memory="1g", cpus=2.0, pids_limit=64, tmp_size="16m")
        joined = " ".join(runner._build_command("c"))
        assert "--memory 1g" in joined
        assert "--cpus 2.0" in joined
        assert "--pids-limit 64" in joined
        assert "size=16m" in joined


class TestVetoList:
    @pytest.mark.parametrize(
        "args",
        [
            ["--network=host"],
            ["--network", "host"],
            ["--net=host"],
            ["-v", "/:/host"],
            ["--volume=/:/host"],
            ["--mount", "type=bind,src=/,dst=/host"],
            ["--privileged"],
            ["--cap-add=SYS_ADMIN"],
            ["--cap-add", "SYS_ADMIN"],
            ["--device=/dev/sda"],
            ["--pid=host"],
            ["--ipc=host"],
            ["--userns=host"],
            ["--security-opt=seccomp=unconfined"],
            ["--dns=1.1.1.1"],
            ["--publish", "8080:80"],
            ["-p", "8080:80"],
            ["--add-host=evil:1.2.3.4"],
            ["--sysctl", "net.ipv4.ip_forward=1"],
            ["--oom-kill-disable"],
            ["--gpus=all"],
            ["--gpus", "all"],
            ["--shm-size=4g"],
            ["--shm-size", "4g"],
        ],
    )
    def test_profile_weakening_args_rejected(self, args):
        with pytest.raises(ValueError, match="airgapped isolation profile"):
            make_runner(extra_args=args)

    @pytest.mark.parametrize(
        ("args", "hint"),
        [
            (["--gpus=all"], "airgapped_gpus"),
            (["--shm-size=4g"], "airgapped_shm_size"),
            (["--mount", "type=bind,src=/,dst=/host"], "airgapped_mounts"),
        ],
    )
    def test_rejection_points_at_the_named_knob(self, args, hint):
        with pytest.raises(ValueError, match=hint):
            make_runner(extra_args=args)

    @pytest.mark.parametrize(
        "args",
        [
            ["--env", "TZ=UTC"],
            ["--user", "1000:1000"],
            ["--tmpfs", "/scratch:rw,size=8m"],
            ["--label", "team=data"],
        ],
    )
    def test_benign_args_pass(self, args):
        runner = make_runner(extra_args=args)
        command = runner._build_command("c")
        assert args[0] in command


class TestCapabilityKnobs:
    """GPUs, read-only mounts, and shm sizing are grantable only through
    their named config keys and always land in the locked section."""

    def test_off_by_default(self):
        command = make_runner()._build_command("c")
        assert "--gpus" not in command
        assert "--shm-size" not in command
        assert "--mount" not in command

    def test_gpus_emitted_when_configured(self):
        command = make_runner(gpus="all")._build_command("c")
        assert "--gpus all" in " ".join(command)

    def test_shm_size_emitted_when_configured(self):
        command = make_runner(shm_size="4g")._build_command("c")
        assert "--shm-size 4g" in " ".join(command)

    def test_mount_emitted_read_only(self, tmp_path):
        runner = make_runner(mounts=[f"{tmp_path}:/models"])
        joined = " ".join(runner._build_command("c"))
        assert f"--mount type=bind,source={tmp_path},target=/models,readonly" in joined

    def test_redundant_ro_suffix_accepted(self, tmp_path):
        runner = make_runner(mounts=[f"{tmp_path}:/models:ro"])
        joined = " ".join(runner._build_command("c"))
        assert "target=/models,readonly" in joined

    def test_rw_suffix_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="always read-only"):
            make_runner(mounts=[f"{tmp_path}:/models:rw"])

    def test_relative_paths_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="absolute"):
            make_runner(mounts=["models:/models"])
        with pytest.raises(ValueError, match="absolute"):
            make_runner(mounts=[f"{tmp_path}:models"])

    def test_missing_host_path_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            make_runner(mounts=[f"{tmp_path / 'nope'}:/models"])

    def test_duplicate_targets_rejected(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        with pytest.raises(ValueError, match="duplicate"):
            make_runner(mounts=[f"{a}:/models", f"{b}:/models"])

    @pytest.mark.parametrize("target", ["/", "/.", "/models/..", "//"])
    def test_root_target_rejected_even_unnormalized(self, target, tmp_path):
        with pytest.raises(ValueError, match="mounting"):
            make_runner(mounts=[f"{tmp_path}:{target}"])

    def test_duplicate_detection_survives_trailing_slash(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        with pytest.raises(ValueError, match="duplicate"):
            make_runner(mounts=[f"{a}:/models", f"{b}:/models/"])

    def test_comma_in_path_rejected(self, tmp_path):
        weird = tmp_path / "a,b"
        weird.mkdir()
        with pytest.raises(ValueError, match="commas"):
            make_runner(mounts=[f"{weird}:/models"])

    def test_malformed_entry_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="expected"):
            make_runner(mounts=[str(tmp_path)])

    def test_knobs_land_in_locked_section(self, tmp_path):
        """Capability grants sit with the profile, after operator extra_args."""
        runner = make_runner(
            extra_args=["--env", "TZ=UTC"],
            gpus="all",
            shm_size="2g",
            mounts=[f"{tmp_path}:/models"],
        )
        command = runner._build_command("c")
        env_at = command.index("--env")
        assert env_at < command.index("--gpus")
        assert env_at < command.index("--shm-size")
        assert env_at < command.index("--mount")


class TestServiceSockets:
    """Named UDS grants: directory contract, mount + env emission, labels."""

    @staticmethod
    def _service_dir(tmp_path, name="inference", with_socket=False, mode=0o555):
        service_dir = tmp_path / name
        service_dir.mkdir()
        if with_socket:
            import socket as socket_mod

            sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
            sock.bind(str(service_dir / "service.sock"))
            sock.close()
        service_dir.chmod(mode)
        return service_dir

    def test_mount_and_env_emitted(self, tmp_path):
        service_dir = self._service_dir(tmp_path)
        runner = make_runner(service_sockets={"inference": str(service_dir)})
        joined = " ".join(runner._build_command("c"))
        assert (
            f"--mount type=bind,source={service_dir},target=/run/flux/services/inference" in joined
        )
        assert (
            '--env FLUX_SERVICE_SOCKETS={"inference":"/run/flux/services/inference/service.sock"}'
            in joined
        )

    def test_service_mount_is_rw_not_readonly(self, tmp_path):
        """UDS connect requires write on the socket inode; the 0555 dir plus
        cap-drop ALL is what keeps the mount write-proof, not the ro flag."""
        service_dir = self._service_dir(tmp_path)
        runner = make_runner(service_sockets={"inference": str(service_dir)})
        command = runner._build_command("c")
        service_mounts = [a for a in command if "flux/services" in a]
        assert service_mounts and all("readonly" not in a for a in service_mounts)

    def test_nothing_emitted_without_grants(self):
        command = make_runner()._build_command("c")
        assert "FLUX_SERVICE_SOCKETS" not in " ".join(command)

    def test_missing_directory_created_write_less(self, tmp_path):
        target = tmp_path / "fresh"
        make_runner(service_sockets={"svc": str(target)})
        assert target.is_dir()
        assert (target.stat().st_mode & 0o777) == 0o555

    def test_writable_directory_rejected(self, tmp_path):
        service_dir = self._service_dir(tmp_path, mode=0o755)
        with pytest.raises(ValueError, match="no write bits"):
            make_runner(service_sockets={"inference": str(service_dir)})

    def test_missing_socket_warns_but_starts(self, tmp_path, caplog):
        service_dir = self._service_dir(tmp_path)
        with caplog.at_level("WARNING"):
            make_runner(service_sockets={"inference": str(service_dir)})
        assert any("no socket" in r.message for r in caplog.records)

    def test_present_socket_no_warning(self, tmp_path, caplog):
        service_dir = self._service_dir(tmp_path, with_socket=True)
        with caplog.at_level("WARNING"):
            make_runner(service_sockets={"inference": str(service_dir)})
        assert not any("no socket" in r.message for r in caplog.records)

    @pytest.mark.parametrize(
        "name",
        ["", "Bad", "with_underscore", "-lead", "trail-", "a--b", "a" * 33],
    )
    def test_invalid_names_rejected(self, name, tmp_path):
        with pytest.raises(ValueError, match="airgapped_service_sockets"):
            make_runner(service_sockets={name: str(tmp_path)})

    def test_uncreatable_directory_names_the_config_entry(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("a file, not a directory")
        with pytest.raises(ValueError, match=r"airgapped_service_sockets\['svc'\].*create"):
            make_runner(service_sockets={"svc": str(blocker / "nested")})

    def test_non_socket_file_warns(self, tmp_path, caplog):
        service_dir = tmp_path / "svc"
        service_dir.mkdir()
        (service_dir / "service.sock").write_text("plain file")
        service_dir.chmod(0o555)
        with caplog.at_level("WARNING"):
            make_runner(service_sockets={"svc": str(service_dir)})
        assert any("not a unix socket" in r.message for r in caplog.records)

    def test_restricted_socket_warns(self, tmp_path, caplog):
        import socket as socket_mod

        service_dir = tmp_path / "svc"
        service_dir.mkdir()
        sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
        sock.bind(str(service_dir / "service.sock"))
        sock.close()
        (service_dir / "service.sock").chmod(0o600)
        service_dir.chmod(0o555)
        with caplog.at_level("WARNING"):
            make_runner(service_sockets={"svc": str(service_dir)})
        assert any("world-connectable" in r.message for r in caplog.records)

    def test_relative_and_comma_paths_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="absolute"):
            make_runner(service_sockets={"svc": "relative/dir"})
        weird = tmp_path / "a,b"
        with pytest.raises(ValueError, match="commas"):
            make_runner(service_sockets={"svc": str(weird)})

    def test_duplicate_directories_rejected(self, tmp_path):
        service_dir = self._service_dir(tmp_path)
        with pytest.raises(ValueError, match="already used"):
            make_runner(
                service_sockets={"one": str(service_dir), "two": str(service_dir) + "/"},
            )

    def test_grants_land_in_locked_section(self, tmp_path):
        service_dir = self._service_dir(tmp_path)
        runner = make_runner(
            extra_args=["--env", "TZ=UTC"],
            service_sockets={"inference": str(service_dir)},
        )
        command = runner._build_command("c")
        env_at = command.index("--env")  # operator's, first
        mount_at = next(i for i, a in enumerate(command) if "flux/services" in a)
        assert env_at < mount_at

    def test_service_names_property(self, tmp_path):
        a = self._service_dir(tmp_path, "svc-a")
        b = self._service_dir(tmp_path, "svc-b")
        runner = make_runner(service_sockets={"svc-b": str(b), "svc-a": str(a)})
        assert runner.service_names == ["svc-a", "svc-b"]


class TestLimitValidation:
    def test_empty_memory_rejected(self):
        with pytest.raises(ValueError, match="airgapped_memory"):
            make_runner(memory="")

    def test_zero_cpus_rejected(self):
        with pytest.raises(ValueError, match="airgapped_cpus"):
            make_runner(cpus=0)

    def test_zero_pids_rejected(self):
        with pytest.raises(ValueError, match="airgapped_pids_limit"):
            make_runner(pids_limit=0)


class TestFactoryWiring:
    def test_known_runner(self):
        assert "docker-airgapped" in KNOWN_RUNNERS

    def _config(self, **overrides):
        cfg = MagicMock()
        cfg.subprocess_term_grace = 5.0
        cfg.docker_image = ""
        cfg.airgapped_image = ""
        cfg.airgapped_memory = "512m"
        cfg.airgapped_cpus = 1.0
        cfg.airgapped_pids_limit = 256
        cfg.airgapped_tmp_size = "64m"
        cfg.airgapped_execution_timeout = 900
        cfg.airgapped_extra_args = []
        cfg.airgapped_gpus = ""
        cfg.airgapped_mounts = []
        cfg.airgapped_shm_size = ""
        cfg.airgapped_service_sockets = {}
        for key, value in overrides.items():
            setattr(cfg, key, value)
        return cfg

    def test_image_fallback_to_docker_image(self):
        with patch.object(DockerRunner, "_verify_docker_available"):
            runners = create_runners(
                ["docker-airgapped"],
                self._config(docker_image="flux:base"),
            )
        runner = runners["docker-airgapped"]
        assert isinstance(runner, AirgappedDockerRunner)
        assert runner.name == "docker-airgapped"
        assert runner._image == "flux:base"

    def test_airgapped_image_wins_over_fallback(self):
        with patch.object(DockerRunner, "_verify_docker_available"):
            runners = create_runners(
                ["docker-airgapped"],
                self._config(docker_image="flux:base", airgapped_image="flux:sealed"),
            )
        assert runners["docker-airgapped"]._image == "flux:sealed"

    def test_no_image_anywhere_fails_at_startup(self):
        with patch.object(DockerRunner, "_verify_docker_available"):
            with pytest.raises(ValueError, match="airgapped_image"):
                create_runners(["docker-airgapped"], self._config())

    def test_capability_knobs_flow_from_config(self, tmp_path):
        with patch.object(DockerRunner, "_verify_docker_available"):
            runners = create_runners(
                ["docker-airgapped"],
                self._config(
                    docker_image="flux:base",
                    airgapped_gpus="device=0",
                    airgapped_mounts=[f"{tmp_path}:/models"],
                    airgapped_shm_size="4g",
                ),
            )
        joined = " ".join(runners["docker-airgapped"]._build_command("c"))
        assert "--gpus device=0" in joined
        assert f"type=bind,source={tmp_path},target=/models,readonly" in joined
        assert "--shm-size 4g" in joined

    def test_service_sockets_flow_from_config(self, tmp_path):
        service_dir = tmp_path / "inference"
        service_dir.mkdir()
        service_dir.chmod(0o555)
        with patch.object(DockerRunner, "_verify_docker_available"):
            runners = create_runners(
                ["docker-airgapped"],
                self._config(
                    docker_image="flux:base",
                    airgapped_service_sockets={"inference": str(service_dir)},
                ),
            )
        runner = runners["docker-airgapped"]
        assert runner.service_names == ["inference"]
        assert "FLUX_SERVICE_SOCKETS" in " ".join(runner._build_command("c"))


class TestExecutionTimeout:
    """The generic wall-clock ceiling, exercised on the subprocess runner
    (same watchdog code path the docker runners inherit)."""

    def _request(self, source: str, name: str, transient: bool = False):
        from flux import ExecutionContext
        from flux.worker import WorkflowDefinition, WorkflowExecutionRequest

        ctx: ExecutionContext = ExecutionContext(
            workflow_id=f"default/{name}",
            workflow_namespace="default",
            workflow_name=name,
        )
        if transient:
            ctx.mark_transient()
        return WorkflowExecutionRequest(
            workflow=WorkflowDefinition(
                id=f"default/{name}",
                namespace="default",
                name=name,
                version=1,
                source=base64.b64encode(textwrap.dedent(source).encode()).decode(),
            ),
            context=ctx,
        )

    def _hooks(self):
        from flux.runners.base import RunnerHooks

        async def checkpoint(ctx):
            pass

        async def get_values(names):
            return {}

        return RunnerHooks(checkpoint=checkpoint, get_secrets=get_values, get_configs=get_values)

    @pytest.mark.asyncio
    async def test_overrunning_execution_raises_timed_out(self):
        source = """
        import asyncio
        from flux import ExecutionContext, workflow

        @workflow
        async def sleeper(ctx: ExecutionContext):
            await asyncio.sleep(60)
        """
        runner = SubprocessRunner(term_grace=2, execution_timeout=2)

        with pytest.raises(ExecutionTimedOut) as excinfo:
            await runner.execute(self._request(source, "sleeper"), self._hooks())
        assert excinfo.value.timeout_seconds == 2
        assert excinfo.value.last_context is not None

    @pytest.mark.asyncio
    async def test_fast_execution_unaffected_by_ceiling(self):
        source = """
        from flux import ExecutionContext, workflow

        @workflow
        async def quick(ctx: ExecutionContext):
            return "done"
        """
        runner = SubprocessRunner(term_grace=5, execution_timeout=60)

        result = await runner.execute(self._request(source, "quick"), self._hooks())
        assert result.has_finished and not result.has_failed

    @pytest.mark.asyncio
    async def test_crash_under_armed_ceiling_stays_a_crash(self):
        """A child that dies on its own must keep WorkerProcessCrashed (and
        its durable-release semantics) even with the watchdog armed — the
        timeout verdict is only for children still alive at the deadline."""
        from flux.errors import WorkerProcessCrashed

        source = """
        import os
        from flux import ExecutionContext, workflow

        @workflow
        async def hard_crash(ctx: ExecutionContext):
            os._exit(9)
        """
        runner = SubprocessRunner(term_grace=5, execution_timeout=60)

        with pytest.raises(WorkerProcessCrashed):
            await runner.execute(self._request(source, "hard_crash"), self._hooks())

    @pytest.mark.asyncio
    async def test_zero_timeout_disables_the_ceiling(self):
        source = """
        from flux import ExecutionContext, workflow

        @workflow
        async def quick2(ctx: ExecutionContext):
            return "done"
        """
        runner = SubprocessRunner(term_grace=5, execution_timeout=0)
        result = await runner.execute(self._request(source, "quick2"), self._hooks())
        assert result.has_finished


class TestWorkerTimeoutMapping:
    """ExecutionTimedOut maps to terminal FAILED for BOTH durabilities —
    never a claim release (a deterministic timeout would re-dispatch
    forever)."""

    def _worker(self):
        from flux.worker import Worker

        worker = Worker.__new__(Worker)
        worker._metrics_collector = None
        checkpoints = []

        async def checkpoint(ctx):
            checkpoints.append(ctx)

        worker._checkpoint = checkpoint
        released = []

        async def release(execution_id):
            released.append(execution_id)

        worker._release_claim = release
        return worker, checkpoints, released

    def _timeout_and_request(self, transient: bool):
        from flux import ExecutionContext
        from flux.worker import WorkflowDefinition, WorkflowExecutionRequest

        ctx: ExecutionContext = ExecutionContext(
            workflow_id="default/wf",
            workflow_namespace="default",
            workflow_name="wf",
            execution_id="exec-timeout",
        )
        if transient:
            ctx.mark_transient()
        request = WorkflowExecutionRequest(
            workflow=WorkflowDefinition(
                id="default/wf",
                namespace="default",
                name="wf",
                version=1,
                source="",
            ),
            context=ctx,
        )
        return ExecutionTimedOut("exec-timeout", 900, last_context=ctx), request

    @pytest.mark.asyncio
    @pytest.mark.parametrize("transient", [False, True])
    async def test_terminal_failed_never_released(self, transient):
        worker, checkpoints, released = self._worker()
        timeout, request = self._timeout_and_request(transient)

        ctx = await worker._handle_runner_timeout(request, timeout)

        assert ctx.has_finished and ctx.has_failed
        assert released == []  # durable path must NOT release for re-dispatch
        assert len(checkpoints) == 1
        assert "ExecutionTimedOut" in str(ctx.output)


@pytest.mark.skipif(not DOCKER_TEST_IMAGE, reason="FLUX_TEST_DOCKER_IMAGE not set")
class TestAirgappedContainerIntegration:
    """Real container: the profile actually holds."""

    def _runner(self, **kwargs):
        return AirgappedDockerRunner(image=DOCKER_TEST_IMAGE, term_grace=5, **kwargs)

    def _request(self, source: str, name: str):
        return TestExecutionTimeout._request(TestExecutionTimeout(), source, name)

    def _hooks(self):
        return TestExecutionTimeout._hooks(TestExecutionTimeout())

    @pytest.mark.asyncio
    async def test_happy_path_completes(self):
        source = """
        from flux import ExecutionContext, workflow

        @workflow
        async def sealed_ok(ctx: ExecutionContext):
            return 21 * 2
        """
        result = await self._runner().execute(self._request(source, "sealed_ok"), self._hooks())
        assert result.has_finished and not result.has_failed
        assert result.output == 42

    @pytest.mark.asyncio
    async def test_network_is_absent(self):
        source = """
        import urllib.request
        from flux import ExecutionContext, workflow

        @workflow
        async def sealed_net(ctx: ExecutionContext):
            urllib.request.urlopen("http://example.com", timeout=5)
        """
        result = await self._runner().execute(self._request(source, "sealed_net"), self._hooks())
        assert result.has_failed  # no interface: the workflow errors, worker survives

    @pytest.mark.asyncio
    async def test_rootfs_read_only_tmp_writable(self):
        source = """
        from flux import ExecutionContext, workflow

        @workflow
        async def sealed_fs(ctx: ExecutionContext):
            with open("/tmp/scratch", "w") as f:
                f.write("ok")
            try:
                open("/persistent", "w")
            except OSError:
                return "read-only held"
            return "rootfs writable!"
        """
        result = await self._runner().execute(self._request(source, "sealed_fs"), self._hooks())
        assert result.output == "read-only held"

    @pytest.mark.asyncio
    async def test_wall_clock_ceiling_kills_container(self):
        source = """
        import asyncio
        from flux import ExecutionContext, workflow

        @workflow
        async def sealed_slow(ctx: ExecutionContext):
            await asyncio.sleep(120)
        """
        runner = self._runner(execution_timeout=3)
        with pytest.raises(ExecutionTimedOut):
            await runner.execute(self._request(source, "sealed_slow"), self._hooks())
