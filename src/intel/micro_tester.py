import asyncio
import logging
from typing import Dict, List, Optional, Any

from telethon import TelegramClient
from telethon.tl.types import Message
from telethon.errors import FloodWaitError

from src.collector.session_pool import SessionPool
from src.storage.models import (
    MicroTest,
    MicroTestFormat,
)
from src.storage.repository import Repository

logger = logging.getLogger(__name__)


# Standard Templates for Micro-Testing formats
FORMAT_TEMPLATES = {
    MicroTestFormat.FORMAT_A: [
        "Ребят, у кого Bybit тоже пингует на переводах через Arbitrum последний час?",
        "Кто-нибудь замечал сегодня проскальзывания в стаканах на споте к фьючерсам?",
        "У всех на OKX вывод USDT без задержек проходит или сеть перегружена?",
    ],
    MicroTestFormat.FORMAT_B: [
        "Там маркет-мейкеры перед экспирацией плотности переставляют, вот и выбило стопы.",
        "Классический сбор стопов роботами. Против их алгоритмов в лоб вставать бессмысленно.",
        "После таких вертолетов обычно еще один пролив рисуют, стакан пустой.",
    ],
    MicroTestFormat.FORMAT_C: [
        "Сканер второй раз за день пингует аномальный спред на азиатских нодах, кто-то замечал?",
        "Вижу по стакану локальную дыру в 2% между площадками, похоже на вывод ликвидности.",
    ],
}


class MicroTester:
    """
    Dispatches Format A/B/C probe messages into target groups
    and tracks telemetry (message survival, replies, admin reactions).
    """

    def __init__(self, repository: Repository, session_pool: SessionPool):
        self.repo = repository
        self.session_pool = session_pool

    async def send_test(
        self,
        chat_identifier: str,
        format_type: MicroTestFormat,
        custom_text: Optional[str] = None,
        reply_to_msg_id: Optional[int] = None,
    ) -> MicroTest:
        """Sends a micro-test message to a chat and records it in the database."""
        text_to_send = custom_text
        if not text_to_send:
            templates = FORMAT_TEMPLATES.get(format_type, [])
            text_to_send = templates[0] if templates else "Тест связи"

        async with self.session_pool.session() as (client, session_name):
            entity = await client.get_entity(chat_identifier)
            chat_id_str = str(entity.id)
            chat_title = getattr(entity, "title", str(chat_identifier))

            logger.info(
                "Sending MicroTest [%s] to chat '%s' (reply_to=%s)",
                format_type.value,
                chat_title,
                reply_to_msg_id,
            )

            sent_msg = await client.send_message(
                entity,
                message=text_to_send,
                reply_to=reply_to_msg_id,
            )

            test_record = MicroTest(
                chat_id=chat_id_str,
                chat_title=chat_title,
                format_type=format_type,
                message_id=sent_msg.id,
                sent_text=text_to_send,
                is_alive=True,
                replies_count=0,
                thread_depth=0,
                admin_reaction=None,
            )

            test_id = await self.repo.save_micro_test(test_record)
            test_record.id = test_id
            return test_record

    async def check_telemetry(self, test_id: int) -> Optional[MicroTest]:
        """
        Checks whether the test message is still alive, whether it was deleted,
        and counts replies or comments in the thread.
        """
        record = await self.repo.get_micro_test(test_id)
        if not record or not record.message_id:
            logger.warning("MicroTest ID %d not found or has no message_id", test_id)
            return None

        async with self.session_pool.session() as (client, session_name):
            try:
                # Retrieve the specific message
                entity = await client.get_entity(int(record.chat_id) if record.chat_id.lstrip("-").isdigit() else record.chat_id)
                messages = await client.get_messages(entity, ids=[record.message_id])

                if not messages or messages[0] is None or getattr(messages[0], "empty", False):
                    # Message was deleted by moderator or anti-spam
                    await self.repo.update_micro_test_telemetry(
                        test_id=test_id,
                        is_alive=False,
                        replies_count=record.replies_count,
                        thread_depth=record.thread_depth,
                        admin_reaction="deleted",
                    )
                    record.is_alive = False
                    record.admin_reaction = "deleted"
                    return record

                msg: Message = messages[0]
                replies_count = 0
                thread_depth = 0

                # Check replies count if available
                if msg.replies:
                    replies_count = getattr(msg.replies, "replies", 0)

                await self.repo.update_micro_test_telemetry(
                    test_id=test_id,
                    is_alive=True,
                    replies_count=replies_count,
                    thread_depth=thread_depth,
                    admin_reaction="none",
                )
                record.is_alive = True
                record.replies_count = replies_count
                record.admin_reaction = "none"
                return record

            except Exception as e:
                logger.error("Error inspecting telemetry for test %d: %s", test_id, e)
                return record
