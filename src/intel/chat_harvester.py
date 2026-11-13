import asyncio
import logging
import random
import re
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

from telethon import TelegramClient
from telethon.tl.types import (
    Channel,
    Chat,
    Message,
    User,
    PeerChannel,
    PeerChat,
)
from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    UserBannedInChannelError,
    FloodWaitError,
)

from config.settings import settings
from src.collector.session_pool import SessionPool
from src.storage.models import GroupMessage
from src.storage.repository import Repository

logger = logging.getLogger(__name__)


# Slang and terminology regex patterns for extraction
CRYPTO_SLANG_PATTERNS = [
    r"\b(?:лонг|шорт|лонг[а-я]*|шорт[а-я]*)\b",
    r"\b(?:стоп|стоп[а-я]*|выбило|сбрили|ликвид|ликвид[а-я]*|рект|rekt)\b",
    r"\b(?:тейк|тейк[а-я]*|протяжк[а-я]*|протянуло|размазало|размазал[а-я]*)\b",
    r"\b(?:стакан|привод|отложк[а-я]*|закол|плотност[а-я]*|ласт\s*прайс|маркировк[а-я]*)\b",
    r"\b(?:бычка|медвежка|альтсезон|флэт|вертолет|пролив|памп|дамп)\b",
    r"\b(?:байбит|бинанс|окикс|bybit|binance|okx|mexc|bitget)\b",
    r"\b(?:арбитраж|нод[а-я]*|спред|фандинг|funding|дельта)\b",
    r"\b(?:разлок|разлок[а-я]*|вестинг|стейкинг|мм|маркетмейкер|комс[а-я]*)\b",
]
SLANG_REGEX = re.compile("|".join(CRYPTO_SLANG_PATTERNS), re.IGNORECASE)


class ChatHarvester:
    """
    Harvests Telegram group messages using OffsetID pagination,
    extracts communication patterns, active contributors, and slang.
    """

    def __init__(self, repository: Repository, session_pool: SessionPool):
        self.repo = repository
        self.session_pool = session_pool

    async def harvest_chat(
        self,
        chat_identifier: str,
        total_limit: int = 500,
        batch_size: int = 100,
        start_offset_id: int = 0,
    ) -> Dict[str, Any]:
        """
        Dumps messages from a chat using OffsetID pagination.
        Saves messages into the repository.
        """
        messages_collected: List[GroupMessage] = []
        offset_id = start_offset_id
        chat_title = None

        logger.info(
            "Starting harvest for chat '%s' (limit: %d, batch: %d, start_offset: %d)",
            chat_identifier,
            total_limit,
            batch_size,
            start_offset_id,
        )

        async with self.session_pool.session() as (client, session_name):
            try:
                target_to_resolve = int(chat_identifier) if chat_identifier.lstrip("-").isdigit() else chat_identifier
                entity = await client.get_entity(target_to_resolve)

                # If target is a broadcast channel without user comments, check for linked discussion chat
                if getattr(entity, "broadcast", False):
                    try:
                        from telethon.tl.functions.channels import GetFullChannelRequest
                        full = await client(GetFullChannelRequest(entity))
                        if full.full_chat.linked_chat_id:
                            linked_entity = await client.get_entity(full.full_chat.linked_chat_id)
                            logger.info(
                                "Target '%s' is a broadcast channel. Switched to linked discussion group '%s' (id: %s)",
                                getattr(entity, "title", chat_identifier),
                                getattr(linked_entity, "title", "Linked Group"),
                                linked_entity.id,
                            )
                            entity = linked_entity
                    except Exception as exc:
                        logger.warning("Could not auto-resolve linked discussion chat: %s", exc)

                chat_title = getattr(entity, "title", str(chat_identifier))
                chat_id_str = str(entity.id)
            except Exception as e:
                logger.error("Failed to resolve chat entity '%s': %s", chat_identifier, e)
                raise

            while len(messages_collected) < total_limit:
                current_batch_limit = min(batch_size, total_limit - len(messages_collected))

                try:
                    history = await client.get_messages(
                        entity,
                        limit=current_batch_limit,
                        offset_id=offset_id,
                    )
                except FloodWaitError as e:
                    logger.warning("FloodWait of %d seconds encountered", e.seconds)
                    raise
                except (ChannelPrivateError, UserBannedInChannelError) as e:
                    logger.error("Access denied to chat '%s': %s", chat_identifier, e)
                    break

                if not history:
                    logger.info("Reached end of history for chat '%s'", chat_identifier)
                    break

                batch_msgs: List[GroupMessage] = []
                for msg in history:
                    if not isinstance(msg, Message) or not msg.text:
                        continue

                    # Sender resolution
                    sender_id = None
                    sender_username = None
                    sender_name = None

                    if msg.sender and isinstance(msg.sender, User):
                        sender_id = msg.sender.id
                        sender_username = msg.sender.username
                        sender_name = f"{msg.sender.first_name or ''} {msg.sender.last_name or ''}".strip() or None

                    reply_to_id = None
                    if msg.reply_to and hasattr(msg.reply_to, "reply_to_msg_id"):
                        reply_to_id = msg.reply_to.reply_to_msg_id

                    batch_msgs.append(
                        GroupMessage(
                            chat_id=chat_id_str,
                            chat_title=chat_title,
                            message_id=msg.id,
                            sender_id=sender_id,
                            sender_username=sender_username,
                            sender_name=sender_name,
                            text=msg.text,
                            reply_to_msg_id=reply_to_id,
                            date=msg.date,
                            has_media=bool(msg.media),
                        )
                    )

                if batch_msgs:
                    await self.repo.save_group_messages(batch_msgs)
                    messages_collected.extend(batch_msgs)

                # Update pagination offset to the last (oldest) message ID in this batch
                offset_id = history[-1].id

                # Anti-flood randomized delay
                delay = random.uniform(settings.fetch_delay_min, settings.fetch_delay_max)
                await asyncio.sleep(delay)

        # Analyze extracted batch
        slang_stats = self.extract_slang(messages_collected)
        hot_threads = await self.repo.get_hot_threads(chat_id_str, min_replies=2, limit=10)
        active_users = await self.repo.get_active_users(chat_id_str, limit=15)

        logger.info(
            "Harvest completed for '%s'. Total messages stored: %d",
            chat_title,
            len(messages_collected),
        )

        return {
            "chat_id": chat_id_str,
            "chat_title": chat_title,
            "total_harvested": len(messages_collected),
            "last_offset_id": offset_id,
            "top_slang": slang_stats[:10],
            "hot_threads_count": len(hot_threads),
            "active_users_count": len(active_users),
        }

    @staticmethod
    def extract_slang(messages: List[GroupMessage]) -> List[Tuple[str, int]]:
        """Extracts frequency of crypto terms and trader slang from messages."""
        terms: List[str] = []
        for msg in messages:
            if not msg.text:
                continue
            matches = SLANG_REGEX.findall(msg.text.lower())
            terms.extend(matches)
        counter = Counter(terms)
        return counter.most_common()
