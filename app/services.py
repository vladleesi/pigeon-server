"""Shared business logic for HTTP and WebSocket routers."""

from __future__ import annotations

import base64
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import get_settings
from .models import (
    Chat,
    ChatMember,
    ChatType,
    Link,
    LinkType,
    User,
)
from .schemas import ChatInfo, ParticipantInfo
from .security import (
    generate_public_id,
    public_key_fingerprint,
)

_settings = get_settings()


def user_to_participant(user: User) -> ParticipantInfo:
    return ParticipantInfo(
        public_id=user.public_id,
        public_key=base64.b64encode(user.public_key).decode("ascii"),
        display_name=user.display_name,
        key_fingerprint=public_key_fingerprint(user.public_key),
    )


async def load_chat_info(session: AsyncSession, chat: Chat) -> ChatInfo:
    result = await session.execute(
        select(ChatMember)
        .where(ChatMember.chat_id == chat.id)
        .options(selectinload(ChatMember.user))
    )
    members = [m.user for m in result.scalars().all() if m.user is not None]
    return ChatInfo(
        id=chat.id,
        chat_type=chat.chat_type.value,
        title=chat.title,
        created_at=chat.created_at,
        participants=[user_to_participant(u) for u in members],
    )


async def _create_user(
    session: AsyncSession, public_key: bytes, display_name: str | None
) -> User:
    user = User(
        public_id=generate_public_id(),
        display_name=(display_name or None),
        public_key=public_key,
    )
    session.add(user)
    await session.flush()
    return user


def _link_expired(link: Link) -> bool:
    if link.expires_at is None:
        return False
    exp = link.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp <= datetime.now(timezone.utc)


async def activate_link(
    session: AsyncSession,
    link_token: str,
    public_key: bytes,
    display_name: str | None,
    current_user: User | None = None,
) -> tuple[User, Chat]:
    """Handle invite-link activation.

    If ``current_user`` is set, add that user to the chat/group.
    Otherwise create a new user with the supplied public key.
    """

    result = await session.execute(
        select(Link).where(Link.token == link_token).with_for_update()
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")
    if not link.is_active:
        raise HTTPException(status_code=410, detail="link is no longer active")
    if _link_expired(link):
        link.is_active = False
        await session.commit()
        raise HTTPException(status_code=410, detail="link expired")
    if link.max_uses and link.uses_count >= link.max_uses:
        link.is_active = False
        await session.commit()
        raise HTTPException(status_code=410, detail="link fully used")

    chat = None
    if link.chat_id is not None:
        chat = await session.get(Chat, link.chat_id)

    if link.link_type is LinkType.personal:
        if chat is None:
            chat = Chat(chat_type=ChatType.personal)
            session.add(chat)
            await session.flush()
            link.chat_id = chat.id
    else:  # group
        if chat is None:
            chat = Chat(chat_type=ChatType.group, title=None)
            session.add(chat)
            await session.flush()
            link.chat_id = chat.id

    # Resolve user.
    if current_user is None:
        user = await _create_user(session, public_key, display_name)
    else:
        user = current_user

    # Skip if already a member.
    result = await session.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat.id,
            ChatMember.user_id == user.id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        # Personal chats cannot exceed two members.
        if link.link_type is LinkType.personal:
            count_result = await session.execute(
                select(ChatMember).where(ChatMember.chat_id == chat.id)
            )
            existing = count_result.scalars().all()
            if len(existing) >= 2:
                raise HTTPException(
                    status_code=410, detail="personal chat is already full"
                )
        session.add(ChatMember(chat_id=chat.id, user_id=user.id))

    link.uses_count += 1
    # Personal links deactivate once fully consumed.
    if link.link_type is LinkType.personal and link.uses_count >= link.max_uses:
        link.is_active = False

    await session.commit()
    await session.refresh(user)
    await session.refresh(chat)
    return user, chat


async def get_user_chats(session: AsyncSession, user: User) -> list[Chat]:
    result = await session.execute(
        select(Chat)
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .where(ChatMember.user_id == user.id)
        .order_by(Chat.created_at.desc())
    )
    return list(result.scalars().all())


async def ensure_chat_member(
    session: AsyncSession, chat_id: int, user: User
) -> Chat:
    result = await session.execute(
        select(Chat)
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .where(Chat.id == chat_id, ChatMember.user_id == user.id)
    )
    chat = result.scalars().first()
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return chat


async def chat_members(session: AsyncSession, chat_id: int) -> list[User]:
    result = await session.execute(
        select(User)
        .join(ChatMember, ChatMember.user_id == User.id)
        .where(ChatMember.chat_id == chat_id)
    )
    return list(result.scalars().all())
