"""Agent definition files apply idempotently (issue #148).

The bugs under test:

- Bug 1 — ``create -f`` inlined ``tools_file``/``workflow_file``/``skills_dir``
  content but ``update -f`` merged the raw YAML over the stored row, replacing
  the inlined source with a path string on every second apply.
- Bug 2 — ``update -f`` merged where the file reads as declarative: a key
  removed from the file survived on the agent.
- Gap 3 — no single idempotent "make the agent match this file" operation.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from flux.cli import _inline_agent_file_refs, cli

TOOLS_SOURCE = "from flux import task\n\n\n@task\nasync def greet(name: str) -> str:\n    return f'hi {name}'\n"


@pytest.fixture
def runner():
    return CliRunner()


def _mock_client(**responses):
    """Build a MagicMock httpx client whose verbs return preset responses."""
    client = MagicMock()
    for verb, response in responses.items():
        getattr(client, verb).return_value = response
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


def _response(status_code=200, body=None):
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    response.json.return_value = body or {}
    return response


def _write_definition(tmp_path, **extra):
    tools_path = tmp_path / "tools.py"
    tools_path.write_text(TOOLS_SOURCE)
    lines = [
        "name: greeter",
        "model: openai/gpt-4o",
        "system_prompt: You greet people.",
        f"tools_file: {tools_path}",
    ]
    lines += [f"{k}: {v}" for k, v in extra.items()]
    definition = tmp_path / "agent.yaml"
    definition.write_text("\n".join(lines) + "\n")
    return definition


class TestInlineHelper:
    def test_multiline_content_passes_through(self):
        data = {"tools_file": TOOLS_SOURCE, "workflow_file": "line1\nline2\n"}
        out = _inline_agent_file_refs(dict(data))
        assert out == data

    def test_bundled_skills_json_passes_through(self):
        bundle = json.dumps({"myskill": {"myskill/SKILL.md": "# skill"}})
        out = _inline_agent_file_refs({"skills_dir": bundle})
        assert out["skills_dir"] == bundle

    def test_missing_path_fails_loudly(self):
        import click

        with pytest.raises(click.ClickException, match="does not exist"):
            _inline_agent_file_refs({"tools_file": "./no_such_tools.py"})

    def test_missing_skills_dir_fails_loudly(self):
        import click

        with pytest.raises(click.ClickException, match="not a directory"):
            _inline_agent_file_refs({"skills_dir": "./no_such_skills"})

    def test_skills_dir_is_bundled(self, tmp_path):
        skill = tmp_path / "skills" / "hello"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# hello skill")
        out = _inline_agent_file_refs({"skills_dir": str(tmp_path / "skills")})
        bundle = json.loads(out["skills_dir"])
        assert bundle["hello"]["hello/SKILL.md"] == "# hello skill"


class TestReapplyDoesNotCorrupt:
    """Bug 1: the same file applied via create and then update must store
    the same inlined content both times."""

    @patch("flux.cli.httpx.Client")
    def test_create_then_update_store_identical_content(self, client_class, runner, tmp_path):
        definition = _write_definition(tmp_path)

        client = _mock_client(post=_response(), put=_response(), get=_response())
        client_class.return_value = client

        result = runner.invoke(cli, ["agent", "create", "greeter", "-f", str(definition)])
        assert result.exit_code == 0, result.output
        created = client.post.call_args.kwargs["json"]
        assert created["tools_file"] == TOOLS_SOURCE

        result = runner.invoke(cli, ["agent", "update", "greeter", "-f", str(definition)])
        assert result.exit_code == 0, result.output
        updated = client.put.call_args.kwargs["json"]
        assert updated["tools_file"] == TOOLS_SOURCE
        assert updated == created


class TestUpdateFileIsDeclarative:
    """Bug 2: with -f the file is the complete desired state — a key absent
    from the file must not survive from the stored definition."""

    @patch("flux.cli.httpx.Client")
    def test_key_removed_from_file_is_removed_from_agent(self, client_class, runner, tmp_path):
        definition = _write_definition(tmp_path)  # no "planning" key

        client = _mock_client(put=_response(), get=_response())
        client_class.return_value = client

        result = runner.invoke(cli, ["agent", "update", "greeter", "-f", str(definition)])
        assert result.exit_code == 0, result.output
        # Replace semantics never consult the stored row...
        client.get.assert_not_called()
        # ...so planning falls back to the model default rather than a stale
        # stored True.
        assert client.put.call_args.kwargs["json"]["planning"] is False

    @patch("flux.cli.httpx.Client")
    def test_flags_still_patch_without_file(self, client_class, runner):
        stored = {
            "name": "greeter",
            "model": "openai/gpt-4o",
            "system_prompt": "You greet people.",
            "planning": True,
        }
        client = _mock_client(put=_response(), get=_response(body=dict(stored)))
        client_class.return_value = client

        result = runner.invoke(cli, ["agent", "update", "greeter", "--model", "openai/gpt-4.1"])
        assert result.exit_code == 0, result.output
        client.get.assert_called_once()
        payload = client.put.call_args.kwargs["json"]
        assert payload["model"] == "openai/gpt-4.1"
        assert payload["planning"] is True  # untouched by the patch


class TestApply:
    """Gap 3: one idempotent operation meaning "make the agent match this
    file"."""

    @patch("flux.cli.httpx.Client")
    def test_apply_updates_when_agent_exists(self, client_class, runner, tmp_path):
        definition = _write_definition(tmp_path)
        client = _mock_client(put=_response(200), post=_response())
        client_class.return_value = client

        result = runner.invoke(cli, ["agent", "apply", "-f", str(definition)])
        assert result.exit_code == 0, result.output
        assert "updated" in result.output
        client.post.assert_not_called()
        assert client.put.call_args.kwargs["json"]["tools_file"] == TOOLS_SOURCE

    @patch("flux.cli.httpx.Client")
    def test_apply_creates_when_agent_missing(self, client_class, runner, tmp_path):
        definition = _write_definition(tmp_path)
        client = _mock_client(put=_response(404), post=_response(200))
        client_class.return_value = client

        result = runner.invoke(cli, ["agent", "apply", "-f", str(definition)])
        assert result.exit_code == 0, result.output
        assert "created" in result.output
        assert client.post.call_args.kwargs["json"]["tools_file"] == TOOLS_SOURCE

    @patch("flux.cli.httpx.Client")
    def test_apply_twice_sends_identical_payloads(self, client_class, runner, tmp_path):
        definition = _write_definition(tmp_path)
        client = _mock_client(put=_response(200))
        client_class.return_value = client

        assert runner.invoke(cli, ["agent", "apply", "-f", str(definition)]).exit_code == 0
        first = client.put.call_args.kwargs["json"]
        assert runner.invoke(cli, ["agent", "apply", "-f", str(definition)]).exit_code == 0
        second = client.put.call_args.kwargs["json"]
        assert first == second

    @patch("flux.cli.httpx.Client")
    def test_apply_recovers_from_create_race(self, client_class, runner, tmp_path):
        definition = _write_definition(tmp_path)
        client = _mock_client()
        # First PUT: 404 (missing) → POST: 409 (someone else created it) →
        # second PUT: 200.
        client.put.side_effect = [_response(404), _response(200)]
        client.post.return_value = _response(409)
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client_class.return_value = client

        result = runner.invoke(cli, ["agent", "apply", "-f", str(definition)])
        assert result.exit_code == 0, result.output
        assert "updated" in result.output
        assert client.put.call_count == 2

    @patch("flux.cli.httpx.Client")
    def test_apply_without_name_anywhere_errors(self, client_class, runner, tmp_path):
        definition = tmp_path / "agent.yaml"
        definition.write_text("model: openai/gpt-4o\nsystem_prompt: hi\n")
        client_class.return_value = _mock_client(put=_response())

        result = runner.invoke(cli, ["agent", "apply", "-f", str(definition)])
        assert result.exit_code != 0
        assert "name missing" in result.output.lower()


class TestFailLoudOnBrokenReference:
    @patch("flux.cli.httpx.Client")
    def test_create_with_missing_tools_file_errors(self, client_class, runner, tmp_path):
        definition = tmp_path / "agent.yaml"
        definition.write_text(
            "name: greeter\nmodel: openai/gpt-4o\nsystem_prompt: hi\ntools_file: ./gone.py\n",
        )
        client = _mock_client(post=_response())
        client_class.return_value = client

        result = runner.invoke(cli, ["agent", "create", "greeter", "-f", str(definition)])
        assert result.exit_code != 0
        assert "does not exist" in result.output
        client.post.assert_not_called()
