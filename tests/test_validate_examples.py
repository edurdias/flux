#!/usr/bin/env python3
"""
Simple validation script for scheduling examples.
Tests the examples directly to ensure they work correctly.
"""

import subprocess
import sys
import os


def run_example(example_path: str) -> tuple[bool, str]:
    """Run an example and return (success, output)"""
    try:
        result = subprocess.run(
            [sys.executable, example_path],
            env={**os.environ, "PYTHONPATH": "."},
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True,
            text=True,
            timeout=30,
        )

        success = result.returncode == 0
        output = result.stdout + result.stderr
        return success, output

    except subprocess.TimeoutExpired:
        return False, "Example timed out after 30 seconds"
    except Exception as e:
        return False, f"Failed to run example: {e}"


def validate_example_output(output: str, example_name: str) -> bool:
    """Validate that example output contains expected patterns"""
    patterns = {
        "daily_report": ["Sales:", "Orders:", "execution_id", "Next run:"],
        "simple_backup": ["Backup result:", "execution_id", "Schedule:"],
        "health_check": ["Health status:", "execution_id", "Schedule:"],
        "data_sync": ["Sync result:", "execution_id", "Schedule:"],
    }

    required_patterns = patterns.get(example_name, [])

    for pattern in required_patterns:
        if pattern not in output:
            print(f"❌ Missing expected pattern '{pattern}' in {example_name} output")
            return False

    return True


def main():
    """Run all example validations"""
    print("🧪 Validating Scheduling Examples...")
    print("=" * 50)

    examples = [
        ("examples/scheduling/daily_report.py", "daily_report"),
        ("examples/scheduling/simple_backup.py", "simple_backup"),
        ("examples/scheduling/health_check.py", "health_check"),
        ("examples/scheduling/data_sync.py", "data_sync"),
    ]

    passed = 0
    failed = 0

    for example_path, example_name in examples:
        print(f"\n🔍 Testing {example_name}...")

        success, output = run_example(example_path)

        if success:
            if validate_example_output(output, example_name):
                print(f"✅ {example_name} - PASSED")
                passed += 1
            else:
                print(f"❌ {example_name} - FAILED (output validation)")
                print(f"Output: {output[:200]}...")
                failed += 1
        else:
            print(f"❌ {example_name} - FAILED (execution)")
            print(f"Error: {output[:200]}...")
            failed += 1

    print("\n" + "=" * 50)
    print(f"📊 Results: {passed} passed, {failed} failed")

    if failed == 0:
        print("🎉 All scheduling examples work correctly!")
        return True
    else:
        print("💥 Some examples failed!")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
