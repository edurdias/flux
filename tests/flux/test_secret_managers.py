"""Tests for the secret manager module."""

from __future__ import annotations
import json

import pytest

from flux.secret_managers import SecretManager


def test_save_and_get_secret():
    """Test saving and retrieving a secret."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Save a test secret
    secret_name = "test_secret"
    secret_value = "test_value"
    secret_manager.save(secret_name, secret_value)

    # Retrieve the secret
    result = secret_manager.get([secret_name])

    # Verify the value is correct
    assert secret_name in result
    assert result[secret_name] == secret_value

    # Clean up
    secret_manager.remove(secret_name)


def test_save_and_update_secret():
    """Test saving and then updating a secret."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Save a test secret
    secret_name = "test_update_secret"
    secret_value = "original_value"
    secret_manager.save(secret_name, secret_value)

    # Update the secret
    updated_value = "updated_value"
    secret_manager.save(secret_name, updated_value)

    # Retrieve the secret
    result = secret_manager.get([secret_name])

    # Verify the value has been updated
    assert secret_name in result
    assert result[secret_name] == updated_value

    # Clean up
    secret_manager.remove(secret_name)


def test_get_multiple_secrets():
    """Test retrieving multiple secrets at once."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Save multiple test secrets
    secret1_name = "test_secret1"
    secret1_value = "value1"
    secret2_name = "test_secret2"
    secret2_value = "value2"

    secret_manager.save(secret1_name, secret1_value)
    secret_manager.save(secret2_name, secret2_value)

    # Retrieve both secrets
    result = secret_manager.get([secret1_name, secret2_name])

    # Verify both values are correct
    assert len(result) == 2
    assert result[secret1_name] == secret1_value
    assert result[secret2_name] == secret2_value

    # Clean up
    secret_manager.remove(secret1_name)
    secret_manager.remove(secret2_name)


def test_get_nonexistent_secret():
    """Test that getting a nonexistent secret raises an error."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Attempt to retrieve a nonexistent secret
    nonexistent_secret = "nonexistent_secret"

    # Ensure any existing secret with this name is removed
    try:
        secret_manager.remove(nonexistent_secret)
    except:  # noqa: E722
        pass

    # Verify that a ValueError is raised
    with pytest.raises(ValueError) as excinfo:
        secret_manager.get([nonexistent_secret])

    # Check that the error message contains the name of the missing secret
    assert nonexistent_secret in str(excinfo.value)


def test_remove_secret():
    """Test removing a secret."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Save a test secret
    secret_name = "test_remove_secret"
    secret_value = "remove_me"
    secret_manager.save(secret_name, secret_value)

    # Verify the secret exists
    result = secret_manager.get([secret_name])
    assert secret_name in result

    # Remove the secret
    secret_manager.remove(secret_name)

    # Verify the secret is gone
    with pytest.raises(ValueError):
        secret_manager.get([secret_name])


def test_all_secrets():
    """Test listing all secrets."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Save some test secrets
    test_secrets = {
        "all_test_secret1": "value1",
        "all_test_secret2": "value2",
        "all_test_secret3": "value3",
    }

    # Clean up any existing test secrets first
    for name in test_secrets.keys():
        try:
            secret_manager.remove(name)
        except:  # noqa: E722
            pass

    # Save the test secrets
    for name, value in test_secrets.items():
        secret_manager.save(name, value)

    # Get all secrets
    all_secrets = secret_manager.all()

    # Verify all test secrets are in the list
    for name in test_secrets.keys():
        assert name in all_secrets

    # Clean up
    for name in test_secrets.keys():
        secret_manager.remove(name)


def test_save_complex_value():
    """Test saving and retrieving a complex object as a secret value."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Create a complex object
    complex_value = {"nested": {"data": [1, 2, 3], "flag": True}, "name": "test"}

    # Save it as a secret
    secret_name = "complex_secret"
    secret_manager.save(secret_name, complex_value)

    # Retrieve the secret
    result = secret_manager.get([secret_name])

    # Verify the complex value is preserved
    assert secret_name in result
    assert result[secret_name] == complex_value
    assert result[secret_name]["nested"]["data"] == [1, 2, 3]
    assert result[secret_name]["nested"]["flag"] is True
    assert result[secret_name]["name"] == "test"

    # Clean up
    secret_manager.remove(secret_name)


def test_remove_nonexistent_secret():
    """Test removing a nonexistent secret (should not raise an error)."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Attempt to remove a nonexistent secret
    nonexistent_secret = "nonexistent_removal_test"

    # Ensure any existing secret with this name is removed first
    try:
        secret_manager.remove(nonexistent_secret)
    except:  # noqa: E722
        pass

    # Removing a nonexistent secret should not raise an error
    secret_manager.remove(nonexistent_secret)  # This should not raise an exception


def test_save_and_get_empty_value():
    """Test saving and retrieving an empty string as a secret value."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Save a test secret with an empty string
    secret_name = "empty_value_secret"
    secret_value = ""
    secret_manager.save(secret_name, secret_value)

    # Retrieve the secret
    result = secret_manager.get([secret_name])

    # Verify the empty value is preserved
    assert secret_name in result
    assert result[secret_name] == secret_value
    assert result[secret_name] == ""

    # Clean up
    secret_manager.remove(secret_name)


def test_save_none_value():
    """Test that saving None as a secret value raises a ValueError."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Save a test secret with None as the value
    secret_name = "none_value_secret"
    secret_value = None

    # Verify that a ValueError is raised when trying to save None
    with pytest.raises(ValueError) as excinfo:
        secret_manager.save(secret_name, secret_value)

    # Check that the error message is as expected
    assert "Secret value cannot be None" in str(excinfo.value)


def test_unicode_secret_name_and_value():
    """Test saving and retrieving a secret with Unicode characters in name and value."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Save a test secret with Unicode characters
    secret_name = "unicode_тест_名前_🔑"
    secret_value = "value_値_价值_🔒"
    secret_manager.save(secret_name, secret_value)

    # Retrieve the secret
    result = secret_manager.get([secret_name])

    # Verify the Unicode value is preserved
    assert secret_name in result
    assert result[secret_name] == secret_value

    # Clean up
    secret_manager.remove(secret_name)


def test_overwrite_and_remove_multiple_secrets():
    """Test overwriting and then removing multiple secrets."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Save multiple test secrets
    test_secrets = {
        "multi_test_1": "initial_value_1",
        "multi_test_2": "initial_value_2",
    }

    # Save the initial values
    for name, value in test_secrets.items():
        secret_manager.save(name, value)

    # Overwrite with new values
    updated_values = {
        "multi_test_1": "updated_value_1",
        "multi_test_2": "updated_value_2",
    }

    for name, value in updated_values.items():
        secret_manager.save(name, value)

    # Retrieve and verify the updated values
    result = secret_manager.get(list(test_secrets.keys()))
    assert len(result) == 2
    assert result["multi_test_1"] == updated_values["multi_test_1"]
    assert result["multi_test_2"] == updated_values["multi_test_2"]

    # Remove the secrets one by one
    for name in list(test_secrets.keys()):
        # First verify we can get the secret before removing it
        assert name in secret_manager.get([name])

        # Remove the secret
        secret_manager.remove(name)

        # Verify the removed secret is gone
        with pytest.raises(ValueError):
            secret_manager.get([name])

    # Verify all secrets are gone
    for name in test_secrets.keys():
        with pytest.raises(ValueError):
            secret_manager.get([name])


def test_save_and_retrieve_json_serializable_data():
    """Test saving and retrieving JSON-serializable data."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Create a JSON-serializable object
    json_data = {
        "string": "text",
        "number": 42,
        "float": 3.14,
        "boolean": True,
        "null": None,
        "array": [1, 2, 3, 4, 5],
        "nested": {"a": "nested value", "b": [{"x": 1}, {"y": 2}]},
    }

    # Save as a secret
    secret_name = "json_secret"
    secret_manager.save(secret_name, json_data)

    # Retrieve the secret
    result = secret_manager.get([secret_name])

    # Verify the JSON data is preserved
    assert secret_name in result
    assert result[secret_name] == json_data

    # Verify we can serialize/deserialize it
    json_string = json.dumps(result[secret_name])
    deserialized = json.loads(json_string)
    assert deserialized == json_data

    # Clean up
    secret_manager.remove(secret_name)


def test_all_secrets_with_no_secrets():
    """Test the all() method when no secrets exist."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    # Create unique test secret names
    test_prefix = "no_secrets_test_"
    test_names = [f"{test_prefix}{i}" for i in range(3)]

    # Clean up any existing test secrets first
    for name in test_names:
        try:
            secret_manager.remove(name)
        except:  # noqa: E722
            pass

    # Get all secrets
    all_secrets = secret_manager.all()

    # Verify none of our test secrets are in the list
    for name in test_names:
        assert name not in all_secrets


def test_same_secret_different_types():
    """Test updating a secret with different data types."""
    # Get the current secret manager
    secret_manager = SecretManager.current()

    secret_name = "type_changing_secret"

    # Test different data types
    test_values = ["string value", 42, 3.14159, True, [1, 2, 3], {"key": "value"}]

    for value in test_values:
        # Save with current value
        secret_manager.save(secret_name, value)

        # Retrieve and verify
        result = secret_manager.get([secret_name])
        assert secret_name in result
        assert result[secret_name] == value
        assert type(result[secret_name]) is type(value)

    # Clean up
    secret_manager.remove(secret_name)
