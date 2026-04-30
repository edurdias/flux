from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ElicitationRequestOutput(BaseModel):
    type: Literal["elicitation"] = "elicitation"
    mode: Literal["url"] = "url"
    elicitation_id: str
    url: str
    message: str
    server_name: str


class ElicitationResponse(BaseModel):
    elicitation_id: str
    action: Literal["accept", "decline", "cancel"]
