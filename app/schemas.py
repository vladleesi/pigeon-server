"""Pydantic schemas for HTTP and WebSocket payloads."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _decode_b64(value: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError("expected a base64 string")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"invalid base64: {exc}") from exc


def _encode_b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


class Base64Field(str):
    """Marker annotation: value is base64-encoded binary."""


class LinkActivateRequest(BaseModel):
    public_key: str = Field(..., description="Client X25519 public key as base64 (32 bytes).")
    display_name: str | None = Field(default=None, max_length=64)

    @field_validator("public_key")
    @classmethod
    def _check_public_key(cls, value: str) -> str:
        data = _decode_b64(value)
        if len(data) != 32:
            raise ValueError("public_key must be 32 bytes (X25519)")
        return value


class ParticipantInfo(BaseModel):
    public_id: str
    public_key: str = Field(..., description="base64")
    display_name: str | None = None
    key_fingerprint: str


class ChatInfo(BaseModel):
    id: int
    chat_type: Literal["personal", "group"]
    title: str | None = None
    created_at: datetime
    participants: list[ParticipantInfo]


class LinkActivateResponse(BaseModel):
    token: str = Field(..., description="Client JWT; send as Authorization: Bearer.")
    user: ParticipantInfo
    chat: ChatInfo


class MeResponse(BaseModel):
    user: ParticipantInfo
    chats: list[ChatInfo]


class MessageEnvelope(BaseModel):
    recipient_public_id: str = Field(..., description="Recipient public_id.")
    ciphertext: str = Field(..., description="Base64 ciphertext (nonce + MAC included).")

    @field_validator("ciphertext")
    @classmethod
    def _ciphertext_is_b64(cls, value: str) -> str:
        _decode_b64(value)
        return value


class SendMessageRequest(BaseModel):
    client_message_id: str = Field(..., min_length=1, max_length=64)
    envelopes: list[MessageEnvelope] = Field(..., min_length=1)


class SendMessageResponse(BaseModel):
    client_message_id: str
    recipients: list[str]
    created_at: datetime


class IncomingMessage(BaseModel):
    id: int
    client_message_id: str
    chat_id: int
    sender_public_id: str
    ciphertext: str = Field(..., description="Base64 ciphertext.")
    created_at: datetime


class IncomingReadReceipt(BaseModel):
    id: int
    client_message_id: str
    chat_id: int
    reader_public_id: str
    created_at: datetime


class PollResponse(BaseModel):
    messages: list[IncomingMessage]
    read_receipts: list[IncomingReadReceipt]


class AckRequest(BaseModel):
    message_ids: list[int] = Field(default_factory=list)
    read_ids: list[int] = Field(default_factory=list)


class MarkReadRequest(BaseModel):
    client_message_ids: list[str] = Field(..., min_length=1, max_length=500)


# ---------- admin API ----------


class AdminLinkCreate(BaseModel):
    link_type: Literal["personal", "group"]
    note: str | None = Field(default=None, max_length=256)
    expires_in_hours: int | None = Field(default=None, ge=1, le=24 * 365)
    group_title: str | None = Field(default=None, max_length=128)


class AdminLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    token: str
    link_type: Literal["personal", "group"]
    url: str
    chat_id: int | None
    max_uses: int
    uses_count: int
    is_active: bool
    note: str | None
    created_at: datetime
    expires_at: datetime | None


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: str
    display_name: str | None
    key_fingerprint: str
    created_at: datetime
    last_seen_at: datetime | None
    is_active: bool
    chats: list[int]


class AdminChatOut(BaseModel):
    id: int
    chat_type: Literal["personal", "group"]
    title: str | None
    created_at: datetime
    member_public_ids: list[str]
    pending_messages: int


class AdminStats(BaseModel):
    users: int
    links_active: int
    chats: int
    pending_messages: int


class ExportedUser(BaseModel):
    public_id: str
    display_name: str | None
    public_key: str
    created_at: datetime
    is_active: bool


class ExportedChatMember(BaseModel):
    public_id: str
    joined_at: datetime


class ExportedChat(BaseModel):
    chat_type: Literal["personal", "group"]
    title: str | None
    created_at: datetime
    members: list[ExportedChatMember]


class ExportedLink(BaseModel):
    token: str
    link_type: Literal["personal", "group"]
    chat_index: int | None
    max_uses: int
    uses_count: int
    is_active: bool
    note: str | None
    created_at: datetime
    expires_at: datetime | None


class ExportBundle(BaseModel):
    version: int = 1
    generated_at: datetime
    users: list[ExportedUser]
    chats: list[ExportedChat]
    links: list[ExportedLink]


__all__ = [
    "_decode_b64",
    "_encode_b64",
    "LinkActivateRequest",
    "LinkActivateResponse",
    "ParticipantInfo",
    "ChatInfo",
    "MeResponse",
    "MessageEnvelope",
    "SendMessageRequest",
    "SendMessageResponse",
    "IncomingMessage",
    "IncomingReadReceipt",
    "PollResponse",
    "AckRequest",
    "MarkReadRequest",
    "AdminLinkCreate",
    "AdminLinkOut",
    "AdminUserOut",
    "AdminChatOut",
    "AdminStats",
    "ExportedUser",
    "ExportedChat",
    "ExportedChatMember",
    "ExportedLink",
    "ExportBundle",
]
