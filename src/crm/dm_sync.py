import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from telethon import TelegramClient
from telethon.tl.types import User, Dialog, Message

from src.collector.session_pool import SessionPool
from src.storage.models import (
    CrmDialogue,
    PipelineType,
    DialogueStatus,
    FastTrackStage,
)
from src.storage.repository import Repository

logger = logging.getLogger(__name__)


class DmSync:
    """
    Synchronizes Telegram personal DM conversations into the CRM
    in read-only mode, tracking conversation depth and recency.
    """

    def __init__(self, repository: Repository, session_pool: SessionPool):
        self.repo = repository
        self.session_pool = session_pool

    async def sync_dialogs(
        self,
        limit_dialogs: int = 50,
        messages_per_dialog: int = 20,
    ) -> List[CrmDialogue]:
        """
        Scans personal DMs from the active Telegram account,
        extracts recent conversation context, and upserts into crm_dialogues.
        """
        synced_dialogues: List[CrmDialogue] = []
        logger.info(
            "Starting DM sync (max dialogs: %d, msgs/dialog: %d)",
            limit_dialogs,
            messages_per_dialog,
        )

        async with self.session_pool.session() as (client, session_name):
            dialogs = await client.get_dialogs(limit=limit_dialogs)

            for d in dialogs:
                # We only sync 1-on-1 personal DMs with real users (exclude bots and channels)
                if not d.is_user:
                    continue

                entity = d.entity
                if not isinstance(entity, User) or entity.bot or entity.is_self:
                    continue

                user_id = entity.id
                username = entity.username
                full_name = f"{entity.first_name or ''} {entity.last_name or ''}".strip() or None

                # Fetch recent messages in this DM
                history = await client.get_messages(entity, limit=messages_per_dialog)
                if not history:
                    continue

                msg_count = len(history)
                last_msg_date = history[0].date if history else datetime.now(timezone.utc)

                # Extract lightweight message snapshot
                history_items = []
                for m in reversed(history):
                    if isinstance(m, Message) and m.text:
                        history_items.append({
                            "id": m.id,
                            "out": bool(m.out),
                            "date": m.date.isoformat() if m.date else None,
                            "text": m.text[:150],
                        })

                # Check if dialogue already exists in CRM
                existing = await self.repo.get_dialogue(user_id, PipelineType.FAST_TRACK)

                if existing:
                    # Keep existing pipeline stage and notes
                    dialogue = CrmDialogue(
                        id=existing.id,
                        lead_user_id=user_id,
                        lead_username=username or existing.lead_username,
                        lead_name=full_name or existing.lead_name,
                        source_chat_id=existing.source_chat_id,
                        pipeline_type=existing.pipeline_type,
                        last_stage_reached=existing.last_stage_reached,
                        status=existing.status,
                        drop_reason=existing.drop_reason,
                        messages_count=max(existing.messages_count, msg_count),
                        last_message_at=last_msg_date,
                        notes=existing.notes,
                        history_json=json.dumps(history_items, ensure_ascii=False),
                    )
                else:
                    dialogue = CrmDialogue(
                        lead_user_id=user_id,
                        lead_username=username,
                        lead_name=full_name,
                        source_chat_id=None,
                        pipeline_type=PipelineType.FAST_TRACK,
                        last_stage_reached=FastTrackStage.IN_DM.value,
                        status=DialogueStatus.ACTIVE,
                        drop_reason=None,
                        messages_count=msg_count,
                        last_message_at=last_msg_date,
                        notes="",
                        history_json=json.dumps(history_items, ensure_ascii=False),
                    )

                await self.repo.upsert_dialogue(dialogue)
                synced_dialogues.append(dialogue)

        logger.info("DM Sync completed. Synced %d dialogue(s)", len(synced_dialogues))
        return synced_dialogues
