from __future__ import annotations

from fastapi import Depends, Header, HTTPException

from flux.security.auth_service import AuthService
from flux.security.errors import AuthenticationError
from flux.security.identity import FluxIdentity
from flux.utils import get_logger

logger = get_logger(__name__)

_auth_service: AuthService | None = None


def init_auth_service(auth_service: AuthService) -> None:
    global _auth_service
    _auth_service = auth_service


def _get_auth_service() -> AuthService | None:
    return _auth_service


async def get_identity(
    authorization: str | None = Header(default=None),
) -> FluxIdentity:
    auth_service = _get_auth_service()
    if auth_service is None:
        from flux.security.identity import ANONYMOUS

        return ANONYMOUS
    token = None
    if authorization:
        if authorization.startswith("Bearer "):
            token = authorization.split(" ", 1)[1]
        else:
            raise HTTPException(
                status_code=401,
                detail="Unsupported authorization scheme. Use 'Bearer <token>'.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    try:
        return await auth_service.authenticate(token)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=e.message)


def require_permission(permission: str):
    async def dependency(
        identity: FluxIdentity = Depends(get_identity),
    ) -> FluxIdentity:
        auth_service = _get_auth_service()
        if auth_service is None:
            return identity
        if not await auth_service.is_authorized(identity, permission):
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: requires '{permission}'",
            )
        return identity

    return dependency
