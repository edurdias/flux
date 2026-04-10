from __future__ import annotations

import json
import os
import stat
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from flux.cli import cli
from flux.cli_auth import get_auth_headers, load_credentials, save_credentials


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

    def test_principals_group_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["principals", "--help"])
        assert result.exit_code == 0
        assert "Principal management" in result.output

    def test_auth_login_no_oidc_config(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["auth", "login"])
        assert result.exit_code == 0
        assert "Error: OIDC not configured" in result.output

    def test_auth_logout(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"access_token": "tok"}')
        with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
            runner = CliRunner()
            result = runner.invoke(cli, ["auth", "logout"])
        assert result.exit_code == 0
        assert "Logged out" in result.output
        assert not creds_file.exists()


class TestSaveCredentials:
    def test_creates_file_with_correct_permissions(self, tmp_path):
        creds_file = tmp_path / ".flux" / "credentials.json"
        token_response = {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
            save_credentials(token_response, "https://auth.example.com/realms/flux")

        assert creds_file.exists()
        file_stat = creds_file.stat()
        mode = stat.S_IMODE(file_stat.st_mode)
        assert mode == (stat.S_IRUSR | stat.S_IWUSR)

    def test_saves_only_refresh_token_no_access_token(self, tmp_path):
        creds_file = tmp_path / ".flux" / "credentials.json"
        token_response = {
            "access_token": "my-access-token",
            "refresh_token": "my-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        issuer = "https://auth.example.com/realms/flux"
        with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
            save_credentials(token_response, issuer)

        data = json.loads(creds_file.read_text())
        # Zero-trust: access token must NEVER be persisted
        assert "access_token" not in data
        assert "expires_at" not in data
        # Only refresh token + metadata is stored
        assert data["refresh_token"] == "my-refresh-token"
        assert data["issuer"] == issuer
        assert data["client_id"] == "flux-api"

    def test_raises_when_no_refresh_token(self, tmp_path):
        creds_file = tmp_path / ".flux" / "credentials.json"
        token_response = {
            "access_token": "tok",
            "expires_in": 3600,
        }
        with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
            with pytest.raises(ValueError, match="refresh_token"):
                save_credentials(token_response, "https://issuer.example.com")

    def test_custom_client_id_persisted(self, tmp_path):
        creds_file = tmp_path / ".flux" / "credentials.json"
        token_response = {
            "refresh_token": "refresh-123",
        }
        with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
            save_credentials(
                token_response,
                "https://issuer.example.com",
                client_id="custom-client",
            )

        data = json.loads(creds_file.read_text())
        assert data["client_id"] == "custom-client"


class TestLoadCredentials:
    def test_returns_none_when_file_missing(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
            result = load_credentials()
        assert result is None

    def test_returns_dict_when_file_valid(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        expected = {
            "refresh_token": "valid-refresh",
            "issuer": "https://auth.example.com",
            "client_id": "flux-api",
        }
        creds_file.write_text(json.dumps(expected))
        with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
            result = load_credentials()
        assert result == expected

    def test_returns_none_on_invalid_json(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text("not valid json {{")
        with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
            result = load_credentials()
        assert result is None


class TestGetAuthHeaders:
    def test_returns_env_var_token_when_set(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        with patch.dict(os.environ, {"FLUX_AUTH_TOKEN": "env-token"}):
            with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
                headers = get_auth_headers()
        assert headers == {"Authorization": "Bearer env-token"}

    def test_returns_fresh_access_token_from_refresh(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        credentials = {
            "refresh_token": "refresh-xyz",
            "issuer": "https://auth.example.com",
            "client_id": "flux-api",
        }
        creds_file.write_text(json.dumps(credentials))
        env = {k: v for k, v in os.environ.items() if k != "FLUX_AUTH_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
                with patch("flux.cli_auth.fetch_access_token", return_value="fresh-access-token"):
                    headers = get_auth_headers()
        assert headers == {"Authorization": "Bearer fresh-access-token"}

    def test_returns_empty_when_refresh_fails(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        credentials = {
            "refresh_token": "refresh-xyz",
            "issuer": "https://auth.example.com",
            "client_id": "flux-api",
        }
        creds_file.write_text(json.dumps(credentials))
        env = {k: v for k, v in os.environ.items() if k != "FLUX_AUTH_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
                with patch("flux.cli_auth.fetch_access_token", return_value=None):
                    headers = get_auth_headers()
        assert headers == {}

    def test_returns_empty_when_no_auth(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        env = {k: v for k, v in os.environ.items() if k != "FLUX_AUTH_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
                headers = get_auth_headers()
        assert headers == {}

    def test_env_var_takes_priority_over_credentials_file(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        credentials = {
            "refresh_token": "refresh-xyz",
            "issuer": "https://auth.example.com",
            "client_id": "flux-api",
        }
        creds_file.write_text(json.dumps(credentials))
        with patch.dict(os.environ, {"FLUX_AUTH_TOKEN": "env-wins"}):
            with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
                headers = get_auth_headers()
        assert headers == {"Authorization": "Bearer env-wins"}


class TestPrincipalsCLI:
    def test_principals_list_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["principals", "list", "--help"])
        assert result.exit_code == 0

    def test_principals_show_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["principals", "show", "--help"])
        assert result.exit_code == 0

    def test_principals_create_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["principals", "create", "--help"])
        assert result.exit_code == 0
        assert "--type" in result.output

    def test_principals_grant_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["principals", "grant", "--help"])
        assert result.exit_code == 0
        assert "--role" in result.output

    def test_principals_revoke_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["principals", "revoke", "--help"])
        assert result.exit_code == 0
        assert "--role" in result.output

    def test_principals_enable_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["principals", "enable", "--help"])
        assert result.exit_code == 0

    def test_principals_disable_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["principals", "disable", "--help"])
        assert result.exit_code == 0

    def test_principals_delete_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["principals", "delete", "--help"])
        assert result.exit_code == 0
        assert "--force" in result.output
        assert "--yes" in result.output

    def test_principals_create_key_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["principals", "create-key", "--help"])
        assert result.exit_code == 0
        assert "--key-name" in result.output

    def test_principals_list_keys_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["principals", "list-keys", "--help"])
        assert result.exit_code == 0

    def test_principals_revoke_key_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["principals", "revoke-key", "--help"])
        assert result.exit_code == 0
        assert "--key-name" in result.output

    def test_principals_list_calls_correct_endpoint(self):
        runner = CliRunner()
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = []
            result = runner.invoke(cli, ["principals", "list"])
        assert result.exit_code == 0
        called_url = mock_get.call_args[0][0]
        assert "/admin/principals" in called_url

    def test_principals_delete_force_without_yes_prompts(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["principals", "delete", "svc-ci", "--force"], input="n\n")
        assert "Cancelled" in result.output

    def test_principals_delete_force_yes_skips_prompt(self):
        runner = CliRunner()
        with patch("httpx.delete") as mock_del:
            mock_del.return_value.status_code = 200
            mock_del.return_value.json.return_value = {}
            result = runner.invoke(cli, ["principals", "delete", "svc-ci", "--force", "--yes"])
        assert "Cancelled" not in result.output
