"""Tests for the config manager module."""

from __future__ import annotations

import json

import pytest

from flux.config_manager import ConfigManager


def test_save_and_get_config():
    manager = ConfigManager.current()
    manager.save("test_config", "test_value")
    result = manager.get(["test_config"])
    assert result["test_config"] == "test_value"
    manager.remove("test_config")


def test_save_and_update_config():
    manager = ConfigManager.current()
    manager.save("test_update", "original")
    manager.save("test_update", "updated")
    result = manager.get(["test_update"])
    assert result["test_update"] == "updated"
    manager.remove("test_update")


def test_get_multiple_configs():
    manager = ConfigManager.current()
    manager.save("multi_1", "value1")
    manager.save("multi_2", "value2")
    result = manager.get(["multi_1", "multi_2"])
    assert len(result) == 2
    assert result["multi_1"] == "value1"
    assert result["multi_2"] == "value2"
    manager.remove("multi_1")
    manager.remove("multi_2")


def test_get_nonexistent_config():
    manager = ConfigManager.current()
    with pytest.raises(ValueError, match="nonexistent"):
        manager.get(["nonexistent"])


def test_remove_config():
    manager = ConfigManager.current()
    manager.save("remove_me", "value")
    manager.remove("remove_me")
    with pytest.raises(ValueError):
        manager.get(["remove_me"])


def test_all_configs():
    manager = ConfigManager.current()
    manager.save("all_test_1", "v1")
    manager.save("all_test_2", "v2")
    all_configs = manager.all()
    assert "all_test_1" in all_configs
    assert "all_test_2" in all_configs
    manager.remove("all_test_1")
    manager.remove("all_test_2")


def test_save_none_value():
    manager = ConfigManager.current()
    with pytest.raises(ValueError, match="cannot be None"):
        manager.save("none_test", None)


def test_save_complex_value():
    manager = ConfigManager.current()
    complex_value = {"nested": {"data": [1, 2, 3]}, "flag": True}
    manager.save("complex", json.dumps(complex_value))
    result = manager.get(["complex"])
    assert json.loads(result["complex"]) == complex_value
    manager.remove("complex")


def test_remove_nonexistent_config():
    manager = ConfigManager.current()
    manager.remove("does_not_exist")
