"""Tests for PostgreSQL data type compatibility."""

import pytest
import base64
from unittest.mock import MagicMock, patch
import dill

from flux.models import EncryptedType, Base64Type
from sqlalchemy import String, TEXT
from sqlalchemy.dialects import postgresql, sqlite


class TestEncryptedType:
    """Test EncryptedType compatibility with PostgreSQL and SQLite."""
    
    def test_load_dialect_impl_postgresql(self):
        """Test EncryptedType uses TEXT for PostgreSQL."""
        encrypted_type = EncryptedType()
        postgres_dialect = postgresql.dialect()
        
        impl = encrypted_type.load_dialect_impl(postgres_dialect)
        assert isinstance(impl, TEXT)
    
    def test_load_dialect_impl_sqlite(self):
        """Test EncryptedType uses String for SQLite."""
        encrypted_type = EncryptedType()
        sqlite_dialect = sqlite.dialect()
        
        impl = encrypted_type.load_dialect_impl(sqlite_dialect)
        assert isinstance(impl, String)
    
    def test_encryption_functionality_preserved(self):
        """Test that encryption functionality works regardless of dialect."""
        encrypted_type = EncryptedType()
        
        # Mock the encryption key
        with patch('flux.config.Configuration.get') as mock_config:
            mock_settings = MagicMock()
            mock_settings.security.encryption_key = "test_encryption_key"
            mock_config.return_value.settings = mock_settings
            
            # Test data
            test_data = {"key": "value", "number": 42}
            
            # Test encryption (bind parameter processing)
            encrypted_value = encrypted_type.process_bind_param(test_data, None)
            assert encrypted_value is not None
            assert isinstance(encrypted_value, str)
            
            # Test decryption (result value processing)
            decrypted_value = encrypted_type.process_result_value(encrypted_value, None)
            assert decrypted_value == test_data
    
    def test_none_value_handling(self):
        """Test handling of None values."""
        encrypted_type = EncryptedType()
        
        # None should pass through unchanged
        assert encrypted_type.process_bind_param(None, None) is None
        assert encrypted_type.process_result_value(None, None) is None
    
    def test_cache_ok_property(self):
        """Test that cache_ok is properly set."""
        encrypted_type = EncryptedType()
        assert encrypted_type.cache_ok is True


class TestBase64Type:
    """Test Base64Type compatibility with PostgreSQL and SQLite."""
    
    def test_load_dialect_impl_postgresql(self):
        """Test Base64Type uses TEXT for PostgreSQL."""
        base64_type = Base64Type()
        postgres_dialect = postgresql.dialect()
        
        impl = base64_type.load_dialect_impl(postgres_dialect)
        assert isinstance(impl, TEXT)
    
    def test_load_dialect_impl_sqlite(self):
        """Test Base64Type uses String for SQLite."""
        base64_type = Base64Type()
        sqlite_dialect = sqlite.dialect()
        
        impl = base64_type.load_dialect_impl(sqlite_dialect)
        assert isinstance(impl, String)
    
    def test_serialization_functionality_preserved(self):
        """Test that serialization functionality works regardless of dialect."""
        base64_type = Base64Type()
        
        # Test data - complex object
        test_data = {
            "workflows": ["workflow1", "workflow2"],
            "metadata": {"version": 1, "created": "2024-01-01"},
            "nested": {"deep": {"value": [1, 2, 3]}}
        }
        
        # Test serialization (bind parameter processing)
        serialized_value = base64_type.process_bind_param(test_data, None)
        assert serialized_value is not None
        assert isinstance(serialized_value, str)
        
        # Verify it's valid base64
        try:
            base64.b64decode(serialized_value)
        except Exception:
            pytest.fail("Serialized value is not valid base64")
        
        # Test deserialization (result value processing)
        deserialized_value = base64_type.process_result_value(serialized_value, None)
        assert deserialized_value == test_data
    
    def test_large_object_serialization(self):
        """Test serialization of large objects (important for PostgreSQL TEXT)."""
        base64_type = Base64Type()
        
        # Create a large test object
        large_data = {
            "large_list": list(range(10000)),
            "large_string": "x" * 50000,
            "nested_structure": {
                f"key_{i}": f"value_{i}" * 100
                for i in range(1000)
            }
        }
        
        # Should handle large objects without issues
        serialized_value = base64_type.process_bind_param(large_data, None)
        assert serialized_value is not None
        assert len(serialized_value) > 100000  # Should be quite large
        
        deserialized_value = base64_type.process_result_value(serialized_value, None)
        assert deserialized_value == large_data
    
    def test_none_value_handling(self):
        """Test handling of None values."""
        base64_type = Base64Type()
        
        # None should pass through unchanged
        assert base64_type.process_bind_param(None, None) is None
        assert base64_type.process_result_value(None, None) is None
    
    def test_cache_ok_property(self):
        """Test that cache_ok is properly set."""
        base64_type = Base64Type()
        assert base64_type.cache_ok is True
    
    def test_dill_protocol_usage(self):
        """Test that Base64Type uses dill with highest protocol."""
        base64_type = Base64Type()
        assert base64_type.protocol == dill.HIGHEST_PROTOCOL


class TestTypeCompatibilityIntegration:
    """Test integration of custom types with different dialects."""
    
    def test_encrypted_type_with_postgres_dialect(self):
        """Test EncryptedType integration with PostgreSQL dialect."""
        from sqlalchemy import MetaData, Table, Column
        
        metadata = MetaData()
        
        # Create table with EncryptedType column
        test_table = Table(
            'test_encrypted',
            metadata,
            Column('id', String, primary_key=True),
            Column('secret_data', EncryptedType())
        )
        
        # Test with PostgreSQL dialect
        postgres_dialect = postgresql.dialect()
        
        # The column should use TEXT type for PostgreSQL
        encrypted_column = test_table.c.secret_data
        impl = encrypted_column.type.load_dialect_impl(postgres_dialect)
        assert isinstance(impl, TEXT)
    
    def test_base64_type_with_postgres_dialect(self):
        """Test Base64Type integration with PostgreSQL dialect."""
        from sqlalchemy import MetaData, Table, Column
        
        metadata = MetaData()
        
        # Create table with Base64Type column
        test_table = Table(
            'test_base64',
            metadata,
            Column('id', String, primary_key=True),
            Column('serialized_data', Base64Type())
        )
        
        # Test with PostgreSQL dialect
        postgres_dialect = postgresql.dialect()
        
        # The column should use TEXT type for PostgreSQL
        base64_column = test_table.c.serialized_data
        impl = base64_column.type.load_dialect_impl(postgres_dialect)
        assert isinstance(impl, TEXT)
    
    def test_type_consistency_across_dialects(self):
        """Test that custom types work consistently across dialects."""
        encrypted_type = EncryptedType()
        base64_type = Base64Type()
        
        # Test data
        test_dict = {"test": "data", "number": 123}
        
        with patch('flux.config.Configuration.get') as mock_config:
            mock_settings = MagicMock()
            mock_settings.security.encryption_key = "test_key"
            mock_config.return_value.settings = mock_settings
            
            # Both types should handle the same data consistently
            # regardless of which dialect they're configured for
            
            # Test EncryptedType
            encrypted_pg = encrypted_type.process_bind_param(test_dict, postgresql.dialect())
            encrypted_sqlite = encrypted_type.process_bind_param(test_dict, sqlite.dialect())
            
            # Both should produce encrypted strings
            assert isinstance(encrypted_pg, str)
            assert isinstance(encrypted_sqlite, str)
            
            # Both should decrypt back to original data
            decrypted_pg = encrypted_type.process_result_value(encrypted_pg, postgresql.dialect())
            decrypted_sqlite = encrypted_type.process_result_value(encrypted_sqlite, sqlite.dialect())
            
            assert decrypted_pg == test_dict
            assert decrypted_sqlite == test_dict
        
        # Test Base64Type
        base64_pg = base64_type.process_bind_param(test_dict, postgresql.dialect())
        base64_sqlite = base64_type.process_bind_param(test_dict, sqlite.dialect())
        
        # Both should produce base64 strings
        assert isinstance(base64_pg, str)
        assert isinstance(base64_sqlite, str)
        
        # Both should deserialize back to original data
        deserialized_pg = base64_type.process_result_value(base64_pg, postgresql.dialect())
        deserialized_sqlite = base64_type.process_result_value(base64_sqlite, sqlite.dialect())
        
        assert deserialized_pg == test_dict
        assert deserialized_sqlite == test_dict


class TestErrorHandling:
    """Test error handling in custom types."""
    
    def test_encrypted_type_encryption_error(self):
        """Test EncryptedType error handling during encryption."""
        encrypted_type = EncryptedType()
        
        # Mock configuration to raise error during encryption
        with patch('flux.config.Configuration.get') as mock_config:
            mock_settings = MagicMock()
            mock_settings.security.encryption_key = None  # Missing key
            mock_config.return_value.settings = mock_settings
            
            with pytest.raises(ValueError, match="Encryption key is not set"):
                encrypted_type.process_bind_param({"test": "data"}, None)
    
    def test_base64_type_serialization_error(self):
        """Test Base64Type error handling during serialization."""
        base64_type = Base64Type()
        
        # Create an object that can't be serialized with dill
        class UnserializableClass:
            def __reduce__(self):
                raise Exception("Cannot serialize this object")
        
        unserializable_obj = UnserializableClass()
        
        with pytest.raises(ValueError, match="Failed to serialize value"):
            base64_type.process_bind_param(unserializable_obj, None)
    
    def test_base64_type_invalid_data_error(self):
        """Test Base64Type error handling with invalid base64 data."""
        base64_type = Base64Type()
        
        invalid_base64 = "not_valid_base64!@#$"
        
        with pytest.raises(ValueError, match="Failed to deserialize value"):
            base64_type.process_result_value(invalid_base64, None)