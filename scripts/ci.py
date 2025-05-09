"""
Scripts for testing GitHub Actions workflows locally.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Define colors for output
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
BLUE = "\033[0;34m"
NC = "\033[0m"  # No Color

# Get the project root directory
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
WORKFLOWS_DIR = PROJECT_ROOT / ".github" / "workflows"
EVENT_FILES_DIR = PROJECT_ROOT / ".github" / "test-events"


def ensure_act_installed() -> str:
    """
    Ensure the act CLI is installed and return its path.
    """
    print(f"{BLUE}Checking if act is installed...{NC}")

    # First check if act is in PATH
    try:
        act_path = subprocess.check_output(["which", "act"]).decode().strip()
        print(f"{GREEN}Found act at: {act_path}{NC}")
        return act_path
    except subprocess.CalledProcessError:
        pass

    # Check for common local bin locations
    local_bin_paths = [
        Path.home() / ".local" / "bin" / "act",
        PROJECT_ROOT / "bin" / "act",
    ]

    for path in local_bin_paths:
        if path.exists():
            print(f"{GREEN}Found act at: {path}{NC}")
            return str(path)

    # If not found, try to install it
    print(f"{YELLOW}act not found. Installing...{NC}")
    try:
        os.makedirs(Path.home() / ".local" / "bin", exist_ok=True)
        install_cmd = [
            "curl",
            "-s",
            "https://raw.githubusercontent.com/nektos/act/master/install.sh",
            "|",
            "bash",
            "-s",
            "--",
            "-b",
            f"{Path.home()}/.local/bin",
        ]
        subprocess.run(" ".join(install_cmd), shell=True, check=True)
        return str(Path.home() / ".local" / "bin" / "act")
    except Exception as e:
        print(f"{RED}Failed to install act: {e}{NC}")
        print(
            f"{YELLOW}Please install act manually: https://github.com/nektos/act#installation{NC}",
        )
        sys.exit(1)


def create_event_files() -> None:
    """
    Create test event files for GitHub Actions.
    """
    print(f"{BLUE}Creating test event files...{NC}")

    # Create the directory if it doesn't exist
    os.makedirs(EVENT_FILES_DIR, exist_ok=True)

    # Pull request event file
    pull_request_event = {
        "pull_request": {"head": {"ref": "feature-branch"}, "base": {"ref": "main"}},
    }

    # Push event file
    push_event = {"ref": "refs/heads/main"}

    # Paths changed event
    paths_event = {
        "ref": "refs/heads/main",
        "commits": [{"modified": ["docs/index.md", "mkdocs.yml"]}],
    }

    # Write the event files
    with open(EVENT_FILES_DIR / "pull_request.json", "w") as f:
        json.dump(pull_request_event, f, indent=2)

    with open(EVENT_FILES_DIR / "push.json", "w") as f:
        json.dump(push_event, f, indent=2)

    with open(EVENT_FILES_DIR / "paths.json", "w") as f:
        json.dump(paths_event, f, indent=2)

    print(f"{GREEN}Event files created in {EVENT_FILES_DIR}{NC}")


def test_workflow(
    workflow: str,
    job: str,
    event: str,
    dryrun: bool = False,
    extra_args: list | None = None,
) -> None:
    """
    Test a specific GitHub Actions workflow locally.

    Args:
        workflow: Name of the workflow file (e.g., 'pr-test.yml')
        job: The job ID to run (e.g., 'test')
        event: The event name (e.g., 'pull_request')
        dryrun: Whether to run in dry-run mode (don't actually run the workflow)
        extra_args: Any additional arguments to pass to act
    """
    # Ensure act is installed
    act_path = ensure_act_installed()

    # Create event files if they don't exist
    if not (EVENT_FILES_DIR / f"{event}.json").exists():
        create_event_files()

    workflow_path = WORKFLOWS_DIR / workflow
    if not workflow_path.exists():
        print(f"{RED}Workflow file not found: {workflow_path}{NC}")
        sys.exit(1)

    print(f"\n{GREEN}=== Testing workflow: {workflow} (Job: {job}) ==={NC}")

    # Prepare the command
    cmd = [
        act_path,
        "-j",
        job,
        "-W",
        str(workflow_path),
        "-e",
        str(EVENT_FILES_DIR / f"{event}.json"),
        "--secret",
        "PYPI_API_TOKEN=fake-token",
    ]

    # Add dry-run flag if needed
    if dryrun:
        cmd.append("--dryrun")

    # Add any extra arguments
    if extra_args:
        cmd.extend(extra_args)

    # Run the command
    try:
        subprocess.run(cmd, check=True)
        print(f"{GREEN}✓ Workflow {workflow} (Job: {job}) passed{NC}")
    except subprocess.CalledProcessError:
        print(f"{RED}✗ Workflow {workflow} (Job: {job}) failed{NC}")
        sys.exit(1)


def test_workflows() -> None:
    """
    Test all GitHub Actions workflows.
    """
    print(f"{BLUE}Testing all GitHub Actions workflows...{NC}")

    # Create the event files
    create_event_files()

    # Test each workflow
    workflows_to_test = [
        ("pr-test.yml", "test", "pull_request"),
        ("build-deploy.yml", "build", "push"),
        ("docs.yml", "deploy", "paths"),
    ]

    for workflow, job, event in workflows_to_test:
        try:
            test_workflow(workflow=workflow, job=job, event=event)
        except Exception as e:
            print(f"{RED}Error testing workflow {workflow}: {e}{NC}")

    print(f"\n{BLUE}All workflow tests completed.{NC}")


if __name__ == "__main__":
    # If the script is run directly, test all workflows
    test_workflows()
