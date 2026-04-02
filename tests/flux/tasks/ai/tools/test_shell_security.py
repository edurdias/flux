from __future__ import annotations

from flux.tasks.ai.tools.shell_security import (
    check_destructive_commands,
    check_fork_bomb,
)


class TestCheckForkBomb:
    def test_detects_classic_fork_bomb(self):
        assert check_fork_bomb(":(){ :|:& };:") is not None

    def test_detects_spaced_fork_bomb(self):
        assert check_fork_bomb(": (  ) { : | : & } ; :") is not None

    def test_detects_while_true_semicolon(self):
        assert check_fork_bomb("while true; do echo x; done") is not None

    def test_detects_while_true_brace(self):
        assert check_fork_bomb("while true { sleep 1 }") is not None

    def test_detects_infinite_for_loop(self):
        assert check_fork_bomb("for (;;) { true; }") is not None

    def test_evasion_mixed_case(self):
        assert check_fork_bomb("While True; do echo; done") is not None

    def test_allows_safe_for_loop(self):
        assert check_fork_bomb("for i in 1 2 3; do echo $i; done") is None

    def test_allows_while_with_condition(self):
        assert check_fork_bomb("while read line; do echo $line; done") is None

    def test_allows_echo(self):
        assert check_fork_bomb("echo hello") is None

    def test_returns_string(self):
        result = check_fork_bomb(":(){ :|:& };:")
        assert isinstance(result, str)


class TestCheckDestructiveCommands:
    def test_detects_rm_rf_root(self):
        assert check_destructive_commands("rm -rf /") is not None

    def test_detects_rm_fr_root(self):
        assert check_destructive_commands("rm -fr /") is not None

    def test_detects_rm_rf_root_star(self):
        assert check_destructive_commands("rm -rf /*") is not None

    def test_detects_mkfs(self):
        assert check_destructive_commands("mkfs.ext4 /dev/sda") is not None

    def test_detects_dd_dev_zero(self):
        assert check_destructive_commands("dd if=/dev/zero of=/dev/sda") is not None

    def test_detects_dd_of_block_device(self):
        assert check_destructive_commands("dd of=/dev/sda") is not None

    def test_detects_redirect_to_sda(self):
        assert check_destructive_commands("> /dev/sda") is not None

    def test_detects_redirect_to_hda(self):
        assert check_destructive_commands("> /dev/hda") is not None

    def test_detects_wipefs(self):
        assert check_destructive_commands("wipefs -a /dev/sda") is not None

    def test_evasion_rm_rf_extra_flags(self):
        assert check_destructive_commands("rm -rf --no-preserve-root /") is not None

    def test_allows_rm_local_dir(self):
        assert check_destructive_commands("rm -rf ./build") is None

    def test_allows_dd_safe_target(self):
        assert check_destructive_commands("dd if=image.iso of=./output.img") is None

    def test_allows_ls(self):
        assert check_destructive_commands("ls -la") is None
