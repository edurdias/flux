from click.testing import CliRunner
from flux.cli import cli


class TestAuthCLI:
    def test_auth_group_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["auth", "--help"])
        assert result.exit_code == 0
        assert "Authentication commands" in result.output

    def test_roles_group_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["roles", "--help"])
        assert result.exit_code == 0
        assert "Role management" in result.output

    def test_service_accounts_group_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["service-accounts", "--help"])
        assert result.exit_code == 0
        assert "Service account" in result.output

    def test_auth_login(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["auth", "login"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output.lower() or "FLUX_AUTH_TOKEN" in result.output

    def test_auth_logout(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["auth", "logout"])
        assert result.exit_code == 0
