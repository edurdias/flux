# Security Considerations

Security is paramount when building distributed workflows that handle sensitive data and integrate with external systems. This guide covers comprehensive security considerations for Flux workflows, including access control, secure execution, and network security.

## Access Control

### Authentication and Authorization

Flux provides multiple layers of authentication and authorization:

```python
from flux.security import require_role, require_permission, authenticate_user

# Role-based access control
@task
@require_role("workflow_operator")
async def operator_task(data: dict):
    """Task that requires operator role"""
    return await perform_operator_action(data)

@task
@require_role(["admin", "power_user"])
async def admin_task(data: dict):
    """Task that requires admin or power_user role"""
    return await perform_admin_action(data)

# Permission-based access control
from flux.security import Permission

@task
@require_permission(Permission.READ_SECRETS)
async def secret_reading_task():
    """Task that requires secret reading permission"""
    api_key = await get_secret("API_KEY")
    return await call_external_api(api_key)

@task
@require_permission([Permission.WRITE_DATA, Permission.MODIFY_WORKFLOWS])
async def data_modification_task(data: dict):
    """Task that requires multiple permissions"""
    return await modify_critical_data(data)
```

### User Context and Identity

Track user identity throughout workflow execution:

```python
@task
async def user_aware_task(ctx: ExecutionContext, data: dict):
    """Task that is aware of user context"""

    # Access user information
    user_id = ctx.user_context.user_id
    user_roles = ctx.user_context.roles
    user_permissions = ctx.user_context.permissions

    # Log user action
    await audit_log.record_action(
        user_id=user_id,
        action="data_processing",
        resource=data.get("resource_id"),
        timestamp=datetime.utcnow()
    )

    # Apply user-specific business logic
    if "admin" in user_roles:
        return await admin_processing(data)
    elif "user" in user_roles:
        return await user_processing(data, user_id)
    else:
        raise PermissionError("Insufficient privileges")

@workflow
async def user_scoped_workflow(ctx: ExecutionContext[dict]):
    """Workflow that operates within user security context"""

    # Validate user has permission to execute this workflow
    if not ctx.user_context.has_permission("execute_workflow"):
        raise PermissionError("User lacks workflow execution permission")

    # Process data with user context
    result = await user_aware_task(ctx, ctx.input)

    return {
        "result": result,
        "executed_by": ctx.user_context.user_id,
        "execution_time": ctx.execution_time
    }
```

### API Key Management

Secure API key handling for workflow access:

```python
from flux.security import APIKeyManager, validate_api_key

class SecureWorkflowAPI:
    def __init__(self):
        self.api_key_manager = APIKeyManager()

    async def authenticate_request(self, api_key: str) -> dict:
        """Authenticate API request and return user context"""

        # Validate API key format
        if not self.is_valid_api_key_format(api_key):
            raise AuthenticationError("Invalid API key format")

        # Check API key against database
        key_info = await self.api_key_manager.validate_key(api_key)

        if not key_info:
            raise AuthenticationError("Invalid or expired API key")

        # Check key permissions and rate limits
        await self.check_rate_limits(key_info["key_id"])

        return {
            "user_id": key_info["user_id"],
            "permissions": key_info["permissions"],
            "rate_limit": key_info["rate_limit"]
        }

    def is_valid_api_key_format(self, api_key: str) -> bool:
        """Validate API key format"""
        # API keys should be 32+ character alphanumeric strings
        return (
            len(api_key) >= 32 and
            api_key.isalnum() and
            api_key.startswith("flux_")
        )

    async def check_rate_limits(self, key_id: str):
        """Check and enforce rate limits"""
        current_usage = await self.api_key_manager.get_usage(key_id)

        if current_usage.requests_per_hour > 1000:
            raise RateLimitError("Hourly rate limit exceeded")

        if current_usage.requests_per_day > 10000:
            raise RateLimitError("Daily rate limit exceeded")
```

### Resource-Based Access Control

Control access to specific resources:

```python
from flux.security import ResourceACL, AccessLevel

@task
async def resource_controlled_task(ctx: ExecutionContext, resource_id: str):
    """Task with resource-level access control"""

    # Check user has access to specific resource
    acl = ResourceACL()

    user_access = await acl.check_access(
        user_id=ctx.user_context.user_id,
        resource_id=resource_id,
        access_level=AccessLevel.READ
    )

    if not user_access:
        raise PermissionError(f"Access denied to resource {resource_id}")

    # Perform operation on authorized resource
    return await process_resource(resource_id)

@task
async def data_classification_task(data: dict):
    """Task that handles data based on classification level"""

    classification = data.get("classification", "public")

    if classification == "confidential":
        # Require special handling for confidential data
        if not ctx.user_context.has_clearance("confidential"):
            raise SecurityError("Insufficient clearance for confidential data")

        return await process_confidential_data(data)
    elif classification == "restricted":
        # Require additional permissions for restricted data
        if not ctx.user_context.has_permission("handle_restricted"):
            raise SecurityError("Permission required for restricted data")

        return await process_restricted_data(data)
    else:
        # Public data can be processed normally
        return await process_public_data(data)
```

## Secure Execution

### Input Validation and Sanitization

Validate and sanitize all inputs to prevent injection attacks:

```python
import re
from typing import Any, Dict
from flux.security import InputValidator, SanitizationError

class WorkflowInputValidator:
    def __init__(self):
        self.validators = {
            "email": self.validate_email,
            "url": self.validate_url,
            "sql_query": self.validate_sql_query,
            "file_path": self.validate_file_path,
            "json_data": self.validate_json_data
        }

    def validate_email(self, email: str) -> str:
        """Validate and sanitize email input"""
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'

        if not re.match(email_pattern, email):
            raise SanitizationError("Invalid email format")

        return email.lower().strip()

    def validate_url(self, url: str) -> str:
        """Validate and sanitize URL input"""
        if not url.startswith(("http://", "https://")):
            raise SanitizationError("URL must start with http:// or https://")

        # Remove potentially dangerous characters
        dangerous_chars = ["<", ">", "\"", "'", "&", ";"]
        for char in dangerous_chars:
            if char in url:
                raise SanitizationError(f"URL contains dangerous character: {char}")

        return url.strip()

    def validate_sql_query(self, query: str) -> str:
        """Validate SQL query for safety"""
        # Check for dangerous SQL keywords
        dangerous_keywords = [
            "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE",
            "EXEC", "EXECUTE", "UNION", "--", "/*", "*/"
        ]

        query_upper = query.upper()
        for keyword in dangerous_keywords:
            if keyword in query_upper:
                raise SanitizationError(f"SQL query contains dangerous keyword: {keyword}")

        return query.strip()

    def validate_file_path(self, path: str) -> str:
        """Validate file path for safety"""
        # Prevent directory traversal attacks
        if ".." in path or path.startswith("/"):
            raise SanitizationError("Invalid file path detected")

        # Only allow alphanumeric, dots, dashes, and underscores
        if not re.match(r'^[a-zA-Z0-9._/-]+$', path):
            raise SanitizationError("File path contains invalid characters")

        return path.strip()

@task
async def secure_input_task(data: dict):
    """Task with comprehensive input validation"""
    validator = WorkflowInputValidator()

    # Validate all inputs
    validated_data = {}

    for key, value in data.items():
        if key.endswith("_email"):
            validated_data[key] = validator.validate_email(value)
        elif key.endswith("_url"):
            validated_data[key] = validator.validate_url(value)
        elif key.endswith("_query"):
            validated_data[key] = validator.validate_sql_query(value)
        elif key.endswith("_path"):
            validated_data[key] = validator.validate_file_path(value)
        else:
            # Apply generic sanitization
            validated_data[key] = str(value).strip()

    return await process_validated_data(validated_data)
```

### Code Injection Prevention

Prevent code injection in dynamic workflows:

```python
import ast
from flux.security import CodeValidator, SecurityViolationError

class SafeCodeValidator:
    """Validates code for safe execution"""

    ALLOWED_NODES = {
        ast.Module, ast.Expr, ast.Name, ast.Load, ast.Constant,
        ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp,
        ast.If, ast.For, ast.While, ast.Return, ast.Assign
    }

    FORBIDDEN_FUNCTIONS = {
        "eval", "exec", "compile", "__import__", "open",
        "file", "input", "raw_input", "execfile"
    }

    def validate_expression(self, expression: str) -> bool:
        """Validate Python expression for safety"""
        try:
            tree = ast.parse(expression, mode='eval')
            return self._check_nodes(tree)
        except SyntaxError:
            raise SecurityViolationError("Invalid Python syntax")

    def validate_code_block(self, code: str) -> bool:
        """Validate Python code block for safety"""
        try:
            tree = ast.parse(code)
            return self._check_nodes(tree)
        except SyntaxError:
            raise SecurityViolationError("Invalid Python syntax")

    def _check_nodes(self, node) -> bool:
        """Recursively check AST nodes for safety"""
        # Check if node type is allowed
        if type(node) not in self.ALLOWED_NODES:
            raise SecurityViolationError(f"Forbidden node type: {type(node).__name__}")

        # Check for forbidden function calls
        if isinstance(node, ast.Name) and node.id in self.FORBIDDEN_FUNCTIONS:
            raise SecurityViolationError(f"Forbidden function: {node.id}")

        # Recursively check child nodes
        for child in ast.iter_child_nodes(node):
            self._check_nodes(child)

        return True

@task
async def dynamic_code_task(code_expression: str, data: dict):
    """Task that safely evaluates dynamic code"""
    validator = SafeCodeValidator()

    # Validate code before execution
    validator.validate_expression(code_expression)

    # Create safe execution environment
    safe_globals = {
        "__builtins__": {},
        "len": len,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "max": max,
        "min": min,
        "sum": sum
    }

    # Execute code in restricted environment
    try:
        result = eval(code_expression, safe_globals, data)
        return result
    except Exception as e:
        raise SecurityViolationError(f"Code execution failed: {e}")
```

### Sandboxed Execution

Run untrusted code in isolated environments:

```python
import docker
import tempfile
import json
from flux.security import SandboxManager

class WorkflowSandbox:
    """Secure sandbox for workflow execution"""

    def __init__(self):
        self.docker_client = docker.from_env()
        self.sandbox_image = "flux-sandbox:latest"

    async def execute_in_sandbox(self, code: str, input_data: dict) -> dict:
        """Execute workflow code in isolated sandbox"""

        # Create temporary directory for code and data
        with tempfile.TemporaryDirectory() as temp_dir:
            # Write code to file
            code_file = f"{temp_dir}/workflow.py"
            with open(code_file, "w") as f:
                f.write(code)

            # Write input data to file
            input_file = f"{temp_dir}/input.json"
            with open(input_file, "w") as f:
                json.dump(input_data, f)

            # Configure sandbox container
            container_config = {
                "image": self.sandbox_image,
                "command": ["python", "/sandbox/workflow.py"],
                "volumes": {temp_dir: {"bind": "/sandbox", "mode": "ro"}},
                "network_mode": "none",  # No network access
                "mem_limit": "512m",     # Memory limit
                "cpu_quota": 50000,      # CPU limit (50% of one core)
                "read_only": True,       # Read-only filesystem
                "security_opt": ["no-new-privileges"],
                "cap_drop": ["ALL"],     # Drop all capabilities
                "user": "nobody"         # Run as non-root user
            }

            # Execute in container
            container = self.docker_client.containers.run(
                detach=True,
                **container_config
            )

            # Wait for completion with timeout
            try:
                exit_code = container.wait(timeout=30)["StatusCode"]
                output = container.logs().decode("utf-8")

                if exit_code != 0:
                    raise SecurityViolationError(f"Sandbox execution failed: {output}")

                # Parse output as JSON result
                return json.loads(output)

            except docker.errors.ContainerError as e:
                raise SecurityViolationError(f"Container execution error: {e}")
            finally:
                container.remove(force=True)

@task
async def sandboxed_user_code_task(user_code: str, input_data: dict):
    """Execute user-provided code in secure sandbox"""
    sandbox = WorkflowSandbox()

    # Additional code validation
    validator = SafeCodeValidator()
    validator.validate_code_block(user_code)

    # Execute in sandbox
    result = await sandbox.execute_in_sandbox(user_code, input_data)

    return {
        "sandbox_result": result,
        "execution_safe": True,
        "validation_passed": True
    }
```

## Network Security

### TLS/SSL Configuration

Ensure all network communications use proper encryption:

```python
import ssl
import httpx
from flux.security import TLSConfig

class SecureNetworkClient:
    """Network client with security configurations"""

    def __init__(self):
        # Create secure SSL context
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = True
        self.ssl_context.verify_mode = ssl.CERT_REQUIRED

        # Configure minimum TLS version
        self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

        # Disable weak ciphers
        self.ssl_context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS')

    async def secure_request(self, url: str, data: dict = None, headers: dict = None):
        """Make secure HTTP request with proper TLS configuration"""

        # Validate URL is HTTPS
        if not url.startswith("https://"):
            raise SecurityViolationError("Only HTTPS URLs are allowed")

        # Configure secure client
        async with httpx.AsyncClient(
            verify=self.ssl_context,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
        ) as client:

            # Add security headers
            secure_headers = {
                "User-Agent": "Flux-Core/1.0",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                **(headers or {})
            }

            if data:
                response = await client.post(url, json=data, headers=secure_headers)
            else:
                response = await client.get(url, headers=secure_headers)

            # Validate response
            response.raise_for_status()

            return response.json()

@task
async def secure_api_call_task(endpoint: str, payload: dict):
    """Task that makes secure API calls"""
    client = SecureNetworkClient()

    # Get API key securely
    api_key = await get_secret("EXTERNAL_API_KEY")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    return await client.secure_request(endpoint, payload, headers)
```

### Network Isolation and Firewall Rules

Configure network isolation for workflow execution:

```python
from flux.security import NetworkPolicy, IPWhitelist

class NetworkSecurityManager:
    """Manage network security policies"""

    def __init__(self):
        self.allowed_domains = {
            "api.trusted-service.com",
            "webhook.partner.com",
            "data.external-provider.com"
        }

        self.blocked_ips = {
            "127.0.0.1",  # Localhost
            "169.254.169.254",  # AWS metadata service
            "metadata.google.internal"  # GCP metadata service
        }

    def validate_target_url(self, url: str) -> bool:
        """Validate target URL against security policy"""
        from urllib.parse import urlparse

        parsed = urlparse(url)

        # Check if domain is allowed
        if parsed.hostname not in self.allowed_domains:
            raise SecurityViolationError(f"Domain not in whitelist: {parsed.hostname}")

        # Check for blocked IPs
        if parsed.hostname in self.blocked_ips:
            raise SecurityViolationError(f"Blocked IP address: {parsed.hostname}")

        # Ensure HTTPS
        if parsed.scheme != "https":
            raise SecurityViolationError("Only HTTPS connections allowed")

        return True

    def create_network_policy(self, workflow_name: str) -> dict:
        """Create network policy for workflow"""
        return {
            "workflow": workflow_name,
            "allowed_domains": list(self.allowed_domains),
            "blocked_ips": list(self.blocked_ips),
            "require_tls": True,
            "max_connections": 10,
            "timeout_seconds": 30
        }

@task
async def network_policy_task(ctx: ExecutionContext, target_url: str):
    """Task with network policy enforcement"""

    security_manager = NetworkSecurityManager()

    # Validate target URL against policy
    security_manager.validate_target_url(target_url)

    # Log network access
    await audit_log.record_network_access(
        workflow_id=ctx.workflow_name,
        execution_id=ctx.execution_id,
        target_url=target_url,
        user_id=ctx.user_context.user_id
    )

    # Make secure request
    client = SecureNetworkClient()
    return await client.secure_request(target_url)
```

### Certificate Validation and Pinning

Implement certificate validation and pinning:

```python
import hashlib
import ssl
from flux.security import CertificateValidator

class CertificatePinning:
    """Certificate pinning for enhanced security"""

    def __init__(self):
        # Store known certificate fingerprints
        self.pinned_certificates = {
            "api.trusted-service.com": [
                "sha256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                "sha256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
            ]
        }

    def validate_certificate(self, hostname: str, cert_der: bytes) -> bool:
        """Validate certificate against pinned fingerprints"""

        # Calculate certificate fingerprint
        fingerprint = hashlib.sha256(cert_der).digest()
        fingerprint_b64 = base64.b64encode(fingerprint).decode()

        # Check against pinned certificates
        pinned_certs = self.pinned_certificates.get(hostname, [])

        if pinned_certs and f"sha256:{fingerprint_b64}" not in pinned_certs:
            raise SecurityViolationError(f"Certificate pinning failed for {hostname}")

        return True

    def create_ssl_context(self, hostname: str) -> ssl.SSLContext:
        """Create SSL context with certificate pinning"""

        context = ssl.create_default_context()

        # Set up certificate validation callback
        def cert_callback(conn, cert, errno, depth, ok):
            if depth == 0:  # Leaf certificate
                cert_der = cert.to_cryptography_cert().public_bytes(
                    serialization.Encoding.DER
                )
                return self.validate_certificate(hostname, cert_der)
            return ok

        context.set_verify(ssl.CERT_REQUIRED, cert_callback)
        return context

@task
async def certificate_pinned_task(hostname: str, endpoint: str):
    """Task with certificate pinning"""

    cert_pinning = CertificatePinning()
    ssl_context = cert_pinning.create_ssl_context(hostname)

    async with httpx.AsyncClient(verify=ssl_context) as client:
        response = await client.get(f"https://{hostname}{endpoint}")
        return response.json()
```

## Data Protection

### Encryption at Rest and in Transit

Implement comprehensive data encryption:

```python
from cryptography.fernet import Fernet
from flux.security import EncryptionManager

class DataEncryption:
    """Handle data encryption for workflows"""

    def __init__(self):
        # Get encryption key from secure storage
        self.encryption_key = self._get_encryption_key()
        self.cipher = Fernet(self.encryption_key)

    def _get_encryption_key(self) -> bytes:
        """Retrieve encryption key securely"""
        # In production, get from secure key management service
        key_string = get_secret("DATA_ENCRYPTION_KEY")
        return key_string.encode()

    def encrypt_sensitive_data(self, data: dict) -> dict:
        """Encrypt sensitive fields in data"""

        sensitive_fields = ["ssn", "credit_card", "password", "token"]
        encrypted_data = data.copy()

        for field in sensitive_fields:
            if field in data:
                # Encrypt sensitive field
                encrypted_value = self.cipher.encrypt(str(data[field]).encode())
                encrypted_data[field] = encrypted_value.decode()
                encrypted_data[f"{field}_encrypted"] = True

        return encrypted_data

    def decrypt_sensitive_data(self, data: dict) -> dict:
        """Decrypt sensitive fields in data"""

        decrypted_data = data.copy()

        for key, value in data.items():
            if key.endswith("_encrypted") and data.get(key) is True:
                field_name = key.replace("_encrypted", "")
                if field_name in data:
                    # Decrypt field
                    decrypted_value = self.cipher.decrypt(data[field_name].encode())
                    decrypted_data[field_name] = decrypted_value.decode()
                    del decrypted_data[key]  # Remove encryption flag

        return decrypted_data

@task
async def encrypted_data_task(ctx: ExecutionContext, sensitive_data: dict):
    """Task that handles encrypted sensitive data"""

    encryption = DataEncryption()

    # Encrypt data before processing
    encrypted_data = encryption.encrypt_sensitive_data(sensitive_data)

    # Log processing without sensitive data
    await audit_log.record_data_processing(
        execution_id=ctx.execution_id,
        data_fields=list(encrypted_data.keys()),
        encrypted_fields=[k for k in encrypted_data.keys() if k.endswith("_encrypted")]
    )

    # Process encrypted data
    result = await process_encrypted_data(encrypted_data)

    # Decrypt result if needed
    if "sensitive_output" in result:
        result = encryption.decrypt_sensitive_data(result)

    return result
```

## Best Practices Summary

### Authentication and Authorization

1. **Multi-Factor Authentication**: Implement MFA for administrative access
2. **Principle of Least Privilege**: Grant minimal necessary permissions
3. **Regular Access Reviews**: Periodically review and update access controls
4. **Strong Password Policies**: Enforce strong authentication credentials

### Secure Development

1. **Input Validation**: Validate and sanitize all inputs
2. **Output Encoding**: Properly encode outputs to prevent injection
3. **Error Handling**: Don't expose sensitive information in error messages
4. **Security Testing**: Regular security testing and vulnerability assessments

### Network Security

1. **TLS Everywhere**: Use TLS for all network communications
2. **Certificate Validation**: Always validate certificates properly
3. **Network Segmentation**: Isolate workflow execution environments
4. **Monitoring**: Monitor network traffic for anomalies

### Data Protection

1. **Encryption**: Encrypt sensitive data at rest and in transit
2. **Key Management**: Use proper key management practices
3. **Data Minimization**: Collect and store only necessary data
4. **Retention Policies**: Implement appropriate data retention policies

### Operational Security

1. **Regular Updates**: Keep all systems and dependencies updated
2. **Security Monitoring**: Implement comprehensive security monitoring
3. **Incident Response**: Have incident response procedures in place
4. **Backup and Recovery**: Maintain secure backup and recovery procedures
