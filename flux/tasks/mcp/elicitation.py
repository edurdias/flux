from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

# An elicitation URL is a page the operator opens to authorize. Anything else
# is a URI handler chosen by the remote server: javascript: runs in the agent
# UI's origin, file:/smb: exfiltrate, custom schemes launch desktop apps.
BROWSABLE_SCHEMES = ("http://", "https://")


def is_browsable(url: str) -> bool:
    """Whether ``url`` is an absolute http(s) address.

    Schemes are case-insensitive (RFC 3986), so ``HTTPS://`` is valid and must
    not be refused.
    """
    return url.lower().startswith(BROWSABLE_SCHEMES)


class ElicitationRequestOutput(BaseModel):
    type: Literal["elicitation"] = "elicitation"
    mode: Literal["url"] = "url"
    elicitation_id: str
    url: str
    message: str
    server_name: str

    @field_validator("url")
    @classmethod
    def validate_browsable(cls, v: str) -> str:
        if v and not is_browsable(v):
            raise ValueError(f"elicitation url must be http(s), got: {v[:32]!r}")
        return v


class ElicitationResponse(BaseModel):
    elicitation_id: str
    action: Literal["accept", "decline", "cancel"]
