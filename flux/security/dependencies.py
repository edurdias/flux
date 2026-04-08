from __future__ import annotations

_auth_service_instance = None


def _get_auth_service():
    return _auth_service_instance


def _set_auth_service(service) -> None:
    global _auth_service_instance
    _auth_service_instance = service
