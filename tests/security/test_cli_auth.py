from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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

    def test_service_accounts_group_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["service-accounts", "--help"])
        assert result.exit_code == 0
        assert "Service account" in result.output

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

    def test_saves_correct_structure(self, tmp_path):
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
        assert data["access_token"] == "my-access-token"
        assert data["refresh_token"] == "my-refresh-token"
        assert data["token_type"] == "Bearer"
        assert data["issuer"] == issuer
        assert "expires_at" in data

    def test_expires_at_is_in_future(self, tmp_path):
        creds_file = tmp_path / ".flux" / "credentials.json"
        token_response = {
            "access_token": "tok",
            "expires_in": 3600,
        }
        with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
            save_credentials(token_response, "https://issuer.example.com")

        data = json.loads(creds_file.read_text())
        expires_at = datetime.fromisoformat(data["expires_at"])
        assert expires_at > datetime.now(timezone.utc)


class TestLoadCredentials:
    def test_returns_none_when_file_missing(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
            result = load_credentials()
        assert result is None

    def test_returns_dict_when_file_valid(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        expected = {
            "access_token": "valid-token",
            "token_type": "Bearer",
            "issuer": "https://auth.example.com",
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

    def test_returns_credentials_token_when_file_exists(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        credentials = {
            "access_token": "stored-token",
            "token_type": "Bearer",
            "expires_at": expires_at,
            "issuer": "https://auth.example.com",
        }
        creds_file.write_text(json.dumps(credentials))
        env = {k: v for k, v in os.environ.items() if k != "FLUX_AUTH_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
                headers = get_auth_headers()
        assert headers == {"Authorization": "Bearer stored-token"}

    def test_returns_empty_when_no_auth(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        env = {k: v for k, v in os.environ.items() if k != "FLUX_AUTH_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
                headers = get_auth_headers()
        assert headers == {}

    def test_env_var_takes_priority_over_credentials_file(self, tmp_path):
        creds_file = tmp_path / "credentials.json"
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        credentials = {
            "access_token": "file-token",
            "token_type": "Bearer",
            "expires_at": expires_at,
            "issuer": "https://auth.example.com",
        }
        creds_file.write_text(json.dumps(credentials))
        with patch.dict(os.environ, {"FLUX_AUTH_TOKEN": "env-wins"}):
            with patch("flux.cli_auth.CREDENTIALS_FILE", creds_file):
                headers = get_auth_headers()
        assert headers == {"Authorization": "Bearer env-wins"}
