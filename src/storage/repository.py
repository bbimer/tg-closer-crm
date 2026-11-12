import asyncio
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from config.settings import settings
from src.storage.models import (
    ALL_DDL_STATEMENTS,
    GroupMessage,
    CrmDialogue,
    MicroTest,
    LeadProspect,
    PipelineType,
    DialogueStatus,
    FastTrackStage,
    DeltaNeutralStage,
    LeadSegment,
    MicroTestFormat,
)

logger = logging.getLogger(__name__)


class Repository:
    """
    Thread-safe SQLite repository using Python's standard library sqlite3
    with WAL mode and async execution via asyncio.to_thread.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or settings.crm_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _sync_init_db(self) -> None:
        with self._get_connection() as conn:
            # 1. Run full DDL statements (creates all tables and indexes)
            for ddl in ALL_DDL_STATEMENTS:
                conn.executescript(ddl)

            # 2. Check and add missing columns if migrating pre-existing legacy databases
            cursor = conn.cursor()
            existing_leads_cols = {col[1] for col in cursor.execute("PRAGMA table_info(leads)").fetchall()}
            if "matched_module" not in existing_leads_cols:
                cursor.execute("ALTER TABLE leads ADD COLUMN matched_module TEXT NOT NULL DEFAULT 'M01_TOKENOMICS_MEV';")
            if "deposit_est" not in existing_leads_cols:
                cursor.execute("ALTER TABLE leads ADD COLUMN deposit_est TEXT NOT NULL DEFAULT '500_2000';")

            existing_crm_cols = {col[1] for col in cursor.execute("PRAGMA table_info(crm_dialogues)").fetchall()}
            if "matched_module" not in existing_crm_cols:
                cursor.execute("ALTER TABLE crm_dialogues ADD COLUMN matched_module TEXT DEFAULT NULL;")
            if "deposit_est" not in existing_crm_cols:
                cursor.execute("ALTER TABLE crm_dialogues ADD COLUMN deposit_est TEXT DEFAULT '500_2000';")
            conn.commit()
            conn.commit()

    async def init_db(self) -> None:
        await asyncio.to_thread(self._sync_init_db)
        logger.info("Database initialized successfully at %s", self.db_path)

    # -------------------------------------------------------------------------
    # Group Messages
    # -------------------------------------------------------------------------

    def _sync_save_group_messages(self, messages: List[GroupMessage]) -> int:
        if not messages:
            return 0
        query = """
        INSERT OR IGNORE INTO group_messages (
            chat_id, chat_title, message_id, sender_id, sender_username, sender_name,
            text, reply_to_msg_id, date, has_media, raw_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = [
            (
                m.chat_id,
                m.chat_title,
                m.message_id,
                m.sender_id,
                m.sender_username,
                m.sender_name,
                m.text or "",
                m.reply_to_msg_id,
                m.date.isoformat() if isinstance(m.date, datetime) else str(m.date),
                1 if m.has_media else 0,
                m.raw_data or "{}",
            )
            for m in messages
        ]
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(query, params)
            conn.commit()
            return cursor.rowcount

    async def save_group_messages(self, messages: List[GroupMessage]) -> int:
        return await asyncio.to_thread(self._sync_save_group_messages, messages)

    def _sync_get_messages_by_chat(
        self, chat_id: str, limit: int, offset: int
    ) -> List[GroupMessage]:
        query = """
        SELECT id, chat_id, chat_title, message_id, sender_id, sender_username,
               sender_name, text, reply_to_msg_id, date, has_media, raw_data, created_at
        FROM group_messages
        WHERE chat_id = ?
        ORDER BY date DESC
        LIMIT ? OFFSET ?
        """
        with self._get_connection() as conn:
            rows = conn.execute(query, (chat_id, limit, offset)).fetchall()
            return [
                GroupMessage(
                    id=row["id"],
                    chat_id=row["chat_id"],
                    chat_title=row["chat_title"],
                    message_id=row["message_id"],
                    sender_id=row["sender_id"],
                    sender_username=row["sender_username"],
                    sender_name=row["sender_name"],
                    text=row["text"],
                    reply_to_msg_id=row["reply_to_msg_id"],
                    date=datetime.fromisoformat(row["date"]) if isinstance(row["date"], str) else row["date"],
                    has_media=bool(row["has_media"]),
                    raw_data=row["raw_data"],
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
                )
                for row in rows
            ]

    async def get_messages_by_chat(
        self, chat_id: str, limit: int = 100, offset: int = 0
    ) -> List[GroupMessage]:
        return await asyncio.to_thread(self._sync_get_messages_by_chat, chat_id, limit, offset)

    def _sync_get_hot_threads(
        self, chat_id: str, min_replies: int, limit: int
    ) -> List[Dict[str, Any]]:
        query = """
        SELECT m.message_id, m.sender_id, m.sender_username, m.sender_name, m.text, m.date,
               COUNT(r.id) as reply_count
        FROM group_messages m
        JOIN group_messages r ON m.chat_id = r.chat_id AND m.message_id = r.reply_to_msg_id
        WHERE m.chat_id = ?
        GROUP BY m.message_id
        HAVING reply_count >= ?
        ORDER BY reply_count DESC
        LIMIT ?
        """
        with self._get_connection() as conn:
            rows = conn.execute(query, (chat_id, min_replies, limit)).fetchall()
            return [dict(row) for row in rows]

    async def get_hot_threads(
        self, chat_id: str, min_replies: int = 3, limit: int = 20
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._sync_get_hot_threads, chat_id, min_replies, limit)

    def _sync_get_active_threads(
        self, chat_id: str, hours: int = 24, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Extracts active discussion threads by recursively stitching reply chains
        (msg 103 -> msg 102 -> msg 101) to their true root in Python.
        """
        from datetime import datetime, timezone, timedelta
        import re

        cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        # Query messages: first try with date cutoff; if fewer than 10 messages, fall back to recent 1000
        with self._get_connection() as conn:
            query_time = """
            SELECT id, chat_id, chat_title, message_id, sender_id, sender_username,
                   sender_name, text, reply_to_msg_id, date
            FROM group_messages
            WHERE chat_id = ? AND date >= ?
            ORDER BY message_id ASC
            """
            rows = conn.execute(query_time, (chat_id, cutoff_iso)).fetchall()
            if len(rows) < 10:
                query_fallback = """
                SELECT id, chat_id, chat_title, message_id, sender_id, sender_username,
                   sender_name, text, reply_to_msg_id, date
                FROM group_messages
                WHERE chat_id = ?
                ORDER BY message_id DESC
                LIMIT 1000
                """
                rows = conn.execute(query_fallback, (chat_id,)).fetchall()
                rows = list(reversed(rows))

        if not rows:
            return []

        # In-memory mapping of all messages in scope
        msg_map: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            msg_map[r["message_id"]] = {
                "message_id": r["message_id"],
                "sender_id": r["sender_id"],
                "sender_username": r["sender_username"],
                "sender_name": r["sender_name"],
                "text": r["text"] or "",
                "reply_to_msg_id": r["reply_to_msg_id"],
                "date": r["date"],
            }

        # Stitch reply trees
        # thread_replies: root_msg_id -> list of child messages in order
        thread_replies: Dict[int, List[Dict[str, Any]]] = {}

        for msg_id, m in msg_map.items():
            parent_id = m["reply_to_msg_id"]
            if not parent_id:
                continue

            # Traverse up the reply ladder to the top-most root
            curr_id = parent_id
            visited = {msg_id}
            root_id = curr_id

            while curr_id in msg_map and curr_id not in visited:
                visited.add(curr_id)
                root_id = curr_id
                parent_m = msg_map[curr_id]
                if parent_m["reply_to_msg_id"]:
                    curr_id = parent_m["reply_to_msg_id"]
                else:
                    break

            if root_id not in thread_replies:
                thread_replies[root_id] = []
            thread_replies[root_id].append(m)

        # Build thread summaries
        results: List[Dict[str, Any]] = []
        ticker_regex = re.compile(r"\b[A-Z0-9]{3,7}\b")

        for root_id, replies in thread_replies.items():
            if len(replies) < 1:
                continue

            root_msg = msg_map.get(root_id)
            root_text = root_msg["text"] if root_msg else "(Сообщение за пределами дампа)"
            root_author = (
                f"@{root_msg['sender_username']}" if root_msg and root_msg["sender_username"]
                else (root_msg["sender_name"] if root_msg and root_msg["sender_name"] else "Admin/Broadcast")
            )

            participants = set()
            if root_msg and root_msg.get("sender_username"):
                participants.add(f"@{root_msg['sender_username']}")
            for rep in replies:
                if rep.get("sender_username"):
                    participants.add(f"@{rep['sender_username']}")

            # Find tickers mentioned in thread
            full_thread_text = root_text + " " + " ".join(r["text"] for r in replies)
            raw_tickers = ticker_regex.findall(full_thread_text)
            common_words = {"AND", "THE", "NOT", "FOR", "BUT", "ALL", "NEW", "USD", "USDT"}
            tickers = sorted(list({t for t in raw_tickers if t not in common_words}))[:4]

            latest_reply = replies[-1]

            results.append({
                "root_msg_id": root_id,
                "root_author": root_author,
                "root_text": root_text[:180] + ("..." if len(root_text) > 180 else ""),
                "reply_count": len(replies),
                "participants_count": len(participants),
                "participants": list(participants)[:5],
                "tickers": tickers,
                "latest_reply_author": f"@{latest_reply['sender_username']}" if latest_reply.get("sender_username") else (latest_reply.get("sender_name") or "User"),
                "latest_reply_text": latest_reply["text"][:140] + ("..." if len(latest_reply["text"]) > 140 else ""),
                "latest_reply_date": latest_reply["date"],
            })

        # Sort by reply count descending
        results.sort(key=lambda x: x["reply_count"], reverse=True)
        return results[:limit]

    async def get_active_threads(
        self, chat_id: str, hours: int = 24, limit: int = 5
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._sync_get_active_threads, chat_id, hours, limit)

    def _sync_get_active_users(self, chat_id: str, limit: int) -> List[Dict[str, Any]]:
        query = """
        SELECT sender_id, sender_username, sender_name,
               COUNT(id) as msg_count,
               MAX(date) as last_seen
        FROM group_messages
        WHERE chat_id = ? AND sender_id IS NOT NULL
        GROUP BY sender_id
        ORDER BY msg_count DESC
        LIMIT ?
        """
        with self._get_connection() as conn:
            rows = conn.execute(query, (chat_id, limit)).fetchall()
            return [dict(row) for row in rows]

    async def get_active_users(
        self, chat_id: str, limit: int = 30
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._sync_get_active_users, chat_id, limit)

    # -------------------------------------------------------------------------
    # Leads / Pain Prospects
    # -------------------------------------------------------------------------

    def _sync_save_lead(self, lead: LeadProspect) -> Optional[int]:
        query = """
        INSERT OR IGNORE INTO leads (
            user_id, username, display_name, source_chat_id, source_chat_title,
            pain_category, matched_module, deposit_est, exchange_mentioned, quote_sample,
            lead_segment, funnel_recommendation, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            lead.user_id,
            lead.username,
            lead.display_name,
            lead.source_chat_id,
            lead.source_chat_title,
            lead.pain_category,
            lead.matched_module,
            lead.deposit_est,
            lead.exchange_mentioned,
            lead.quote_sample,
            lead.lead_segment.value if isinstance(lead.lead_segment, LeadSegment) else lead.lead_segment,
            lead.funnel_recommendation.value if isinstance(lead.funnel_recommendation, PipelineType) else lead.funnel_recommendation,
            lead.status,
        )
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.lastrowid

    async def save_lead(self, lead: LeadProspect) -> Optional[int]:
        return await asyncio.to_thread(self._sync_save_lead, lead)

    def _sync_save_leads(self, leads: List[LeadProspect]) -> int:
        if not leads:
            return 0
        inserted = 0
        for lead in leads:
            res = self._sync_save_lead(lead)
            if res and res > 0:
                inserted += 1
        return inserted

    async def save_leads(self, leads: List[LeadProspect]) -> int:
        return await asyncio.to_thread(self._sync_save_leads, leads)

    def _sync_get_lead(self, user_id: int) -> Optional[LeadProspect]:
        query = """
        SELECT id, user_id, username, display_name, source_chat_id, source_chat_title,
               pain_category, matched_module, deposit_est, exchange_mentioned, quote_sample,
               lead_segment, funnel_recommendation, status, detected_at
        FROM leads
        WHERE user_id = ? OR id = ?
        ORDER BY detected_at DESC
        LIMIT 1
        """
        with self._get_connection() as conn:
            row = conn.execute(query, (user_id, user_id)).fetchone()
            if not row:
                return None
            return LeadProspect(
                id=row["id"],
                user_id=row["user_id"],
                username=row["username"],
                display_name=row["display_name"],
                source_chat_id=row["source_chat_id"],
                source_chat_title=row["source_chat_title"],
                pain_category=row["pain_category"],
                matched_module=row["matched_module"] if "matched_module" in row.keys() else "M01_TOKENOMICS_MEV",
                deposit_est=row["deposit_est"] if "deposit_est" in row.keys() else "500_2000",
                exchange_mentioned=row["exchange_mentioned"],
                quote_sample=row["quote_sample"],
                lead_segment=LeadSegment(row["lead_segment"]),
                funnel_recommendation=PipelineType(row["funnel_recommendation"]),
                status=row["status"],
                detected_at=datetime.fromisoformat(row["detected_at"]) if row["detected_at"] else None,
            )

    async def get_lead(self, user_id: int) -> Optional[LeadProspect]:
        return await asyncio.to_thread(self._sync_get_lead, user_id)

    def _sync_get_leads(
        self,
        segment: Optional[str],
        pain_category: Optional[str],
        status: Optional[str],
        limit: int,
    ) -> List[LeadProspect]:
        conditions = []
        params: List[Any] = []
        if segment:
            conditions.append("lead_segment = ?")
            params.append(segment)
        if pain_category:
            conditions.append("pain_category = ?")
            params.append(pain_category)
        if status:
            conditions.append("status = ?")
            params.append(status)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
        SELECT id, user_id, username, display_name, source_chat_id, source_chat_title,
               pain_category, matched_module, deposit_est, exchange_mentioned, quote_sample,
               lead_segment, funnel_recommendation, status, detected_at
        FROM leads
        {where_clause}
        ORDER BY detected_at DESC
        LIMIT ?
        """
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [
                LeadProspect(
                    id=row["id"],
                    user_id=row["user_id"],
                    username=row["username"],
                    display_name=row["display_name"],
                    source_chat_id=row["source_chat_id"],
                    source_chat_title=row["source_chat_title"],
                    pain_category=row["pain_category"],
                    matched_module=row["matched_module"] if "matched_module" in row.keys() else "M01_TOKENOMICS_MEV",
                    deposit_est=row["deposit_est"] if "deposit_est" in row.keys() else "500_2000",
                    exchange_mentioned=row["exchange_mentioned"],
                    quote_sample=row["quote_sample"],
                    lead_segment=LeadSegment(row["lead_segment"]),
                    funnel_recommendation=PipelineType(row["funnel_recommendation"]),
                    status=row["status"],
                    detected_at=datetime.fromisoformat(row["detected_at"]) if row["detected_at"] else None,
                )
                for row in rows
            ]

    async def get_leads(
        self,
        segment: Optional[str] = None,
        pain_category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[LeadProspect]:
        return await asyncio.to_thread(self._sync_get_leads, segment, pain_category, status, limit)

    # -------------------------------------------------------------------------
    # CRM Dialogues & Pipeline Tracker
    # -------------------------------------------------------------------------

    def _sync_upsert_dialogue(self, dialogue: CrmDialogue) -> int:
        query = """
        INSERT INTO crm_dialogues (
            lead_user_id, lead_username, lead_name, source_chat_id,
            pipeline_type, last_stage_reached, status, drop_reason,
            matched_module, deposit_est,
            messages_count, last_message_at, notes, history_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(lead_user_id, pipeline_type) DO UPDATE SET
            lead_username = excluded.lead_username,
            lead_name = excluded.lead_name,
            source_chat_id = COALESCE(crm_dialogues.source_chat_id, excluded.source_chat_id),
            matched_module = COALESCE(crm_dialogues.matched_module, excluded.matched_module),
            deposit_est = COALESCE(crm_dialogues.deposit_est, excluded.deposit_est),
            messages_count = excluded.messages_count,
            last_message_at = excluded.last_message_at,
            history_json = excluded.history_json,
            updated_at = CURRENT_TIMESTAMP
        """
        pipeline_val = (
            dialogue.pipeline_type.value
            if isinstance(dialogue.pipeline_type, PipelineType)
            else dialogue.pipeline_type
        )
        status_val = (
            dialogue.status.value
            if isinstance(dialogue.status, DialogueStatus)
            else dialogue.status
        )
        last_msg_at = (
            dialogue.last_message_at.isoformat()
            if isinstance(dialogue.last_message_at, datetime)
            else dialogue.last_message_at
        )

        params = (
            dialogue.lead_user_id,
            dialogue.lead_username,
            dialogue.lead_name,
            dialogue.source_chat_id,
            pipeline_val,
            dialogue.last_stage_reached,
            status_val,
            dialogue.drop_reason,
            dialogue.matched_module,
            dialogue.deposit_est,
            dialogue.messages_count,
            last_msg_at,
            dialogue.notes,
            dialogue.history_json,
        )
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.lastrowid

    async def upsert_dialogue(self, dialogue: CrmDialogue) -> int:
        return await asyncio.to_thread(self._sync_upsert_dialogue, dialogue)

    def _sync_get_dialogue(
        self, lead_user_id: int, pipeline_type: PipelineType
    ) -> Optional[CrmDialogue]:
        query = """
        SELECT id, lead_user_id, lead_username, lead_name, source_chat_id,
               pipeline_type, last_stage_reached, status, drop_reason,
               matched_module, deposit_est,
               messages_count, last_message_at, notes, history_json, created_at, updated_at
        FROM crm_dialogues
        WHERE lead_user_id = ? AND pipeline_type = ?
        """
        with self._get_connection() as conn:
            row = conn.execute(query, (lead_user_id, pipeline_type.value)).fetchone()
            if not row:
                return None
            return CrmDialogue(
                id=row["id"],
                lead_user_id=row["lead_user_id"],
                lead_username=row["lead_username"],
                lead_name=row["lead_name"],
                source_chat_id=row["source_chat_id"],
                pipeline_type=PipelineType(row["pipeline_type"]),
                last_stage_reached=row["last_stage_reached"],
                status=DialogueStatus(row["status"]),
                drop_reason=row["drop_reason"],
                matched_module=row["matched_module"] if "matched_module" in row.keys() else None,
                deposit_est=row["deposit_est"] if "deposit_est" in row.keys() else "500_2000",
                messages_count=row["messages_count"],
                last_message_at=datetime.fromisoformat(row["last_message_at"]) if row["last_message_at"] else None,
                notes=row["notes"],
                history_json=row["history_json"],
                created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
                updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            )

    async def get_dialogue(
        self, lead_user_id: int, pipeline_type: PipelineType = PipelineType.FAST_TRACK
    ) -> Optional[CrmDialogue]:
        return await asyncio.to_thread(self._sync_get_dialogue, lead_user_id, pipeline_type)

    def _sync_get_dialogues_by_pipeline(
        self, pipeline_type: PipelineType, status: Optional[str]
    ) -> List[CrmDialogue]:
        conditions = ["pipeline_type = ?"]
        params: List[Any] = [pipeline_type.value]
        if status:
            conditions.append("status = ?")
            params.append(status)

        query = f"""
        SELECT id, lead_user_id, lead_username, lead_name, source_chat_id,
               pipeline_type, last_stage_reached, status, drop_reason,
               messages_count, last_message_at, notes, history_json, created_at, updated_at
        FROM crm_dialogues
        WHERE {' AND '.join(conditions)}
        ORDER BY updated_at DESC
        """
        with self._get_connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [
                CrmDialogue(
                    id=row["id"],
                    lead_user_id=row["lead_user_id"],
                    lead_username=row["lead_username"],
                    lead_name=row["lead_name"],
                    source_chat_id=row["source_chat_id"],
                    pipeline_type=PipelineType(row["pipeline_type"]),
                    last_stage_reached=row["last_stage_reached"],
                    status=DialogueStatus(row["status"]),
                    drop_reason=row["drop_reason"],
                    messages_count=row["messages_count"],
                    last_message_at=datetime.fromisoformat(row["last_message_at"]) if row["last_message_at"] else None,
                    notes=row["notes"],
                    history_json=row["history_json"],
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
                    updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
                )
                for row in rows
            ]

    async def get_dialogues_by_pipeline(
        self, pipeline_type: PipelineType, status: Optional[str] = None
    ) -> List[CrmDialogue]:
        return await asyncio.to_thread(self._sync_get_dialogues_by_pipeline, pipeline_type, status)

    def _sync_update_dialogue_stage(
        self,
        lead_user_id: int,
        new_stage: str,
        pipeline_type: PipelineType,
        drop_reason: Optional[str],
        status: Optional[DialogueStatus],
        notes: Optional[str],
    ) -> bool:
        updates = ["last_stage_reached = ?", "updated_at = CURRENT_TIMESTAMP"]
        params: List[Any] = [new_stage]

        if drop_reason is not None:
            updates.append("drop_reason = ?")
            params.append(drop_reason)

        if status is not None:
            updates.append("status = ?")
            params.append(status.value if isinstance(status, DialogueStatus) else status)

        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)

        params.extend([lead_user_id, pipeline_type.value])

        query = f"""
        UPDATE crm_dialogues
        SET {', '.join(updates)}
        WHERE lead_user_id = ? AND pipeline_type = ?
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            conn.commit()
            return cursor.rowcount > 0

    async def update_dialogue_stage(
        self,
        lead_user_id: int,
        new_stage: str,
        pipeline_type: PipelineType = PipelineType.FAST_TRACK,
        drop_reason: Optional[str] = None,
        status: Optional[DialogueStatus] = None,
        notes: Optional[str] = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._sync_update_dialogue_stage,
            lead_user_id,
            new_stage,
            pipeline_type,
            drop_reason,
            status,
            notes,
        )

    def _sync_get_pipeline_stats(self, pipeline_type: PipelineType) -> Dict[str, Any]:
        with self._get_connection() as conn:
            stage_query = """
            SELECT last_stage_reached, count(*) as count
            FROM crm_dialogues
            WHERE pipeline_type = ?
            GROUP BY last_stage_reached
            """
            stages_distribution = {
                row["last_stage_reached"]: row["count"]
                for row in conn.execute(stage_query, (pipeline_type.value,)).fetchall()
            }

            status_query = """
            SELECT status, count(*) as count
            FROM crm_dialogues
            WHERE pipeline_type = ?
            GROUP BY status
            """
            status_distribution = {
                row["status"]: row["count"]
                for row in conn.execute(status_query, (pipeline_type.value,)).fetchall()
            }

            drop_query = """
            SELECT drop_reason, count(*) as count
            FROM crm_dialogues
            WHERE pipeline_type = ? AND drop_reason IS NOT NULL
            GROUP BY drop_reason
            ORDER BY count DESC
            """
            drop_reasons = {
                row["drop_reason"]: row["count"]
                for row in conn.execute(drop_query, (pipeline_type.value,)).fetchall()
            }

            total_dialogues = sum(status_distribution.values())

            return {
                "pipeline_type": pipeline_type.value,
                "total_dialogues": total_dialogues,
                "stages": stages_distribution,
                "status": status_distribution,
                "drop_reasons": drop_reasons,
            }

    async def get_pipeline_stats(self, pipeline_type: PipelineType) -> Dict[str, Any]:
        return await asyncio.to_thread(self._sync_get_pipeline_stats, pipeline_type)

    # -------------------------------------------------------------------------
    # Micro Tests
    # -------------------------------------------------------------------------

    def _sync_save_micro_test(self, test: MicroTest) -> int:
        query = """
        INSERT INTO micro_tests (
            chat_id, chat_title, format_type, message_id, sent_text,
            is_alive, replies_count, thread_depth, admin_reaction
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            test.chat_id,
            test.chat_title,
            test.format_type.value if isinstance(test.format_type, MicroTestFormat) else test.format_type,
            test.message_id,
            test.sent_text,
            1 if test.is_alive else 0,
            test.replies_count,
            test.thread_depth,
            test.admin_reaction,
        )
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.lastrowid

    async def save_micro_test(self, test: MicroTest) -> int:
        return await asyncio.to_thread(self._sync_save_micro_test, test)

    def _sync_get_micro_test(self, test_id: int) -> Optional[MicroTest]:
        query = """
        SELECT id, chat_id, chat_title, format_type, message_id, sent_text,
               is_alive, replies_count, thread_depth, admin_reaction, sent_at, checked_at
        FROM micro_tests
        WHERE id = ?
        """
        with self._get_connection() as conn:
            row = conn.execute(query, (test_id,)).fetchone()
            if not row:
                return None
            return MicroTest(
                id=row["id"],
                chat_id=row["chat_id"],
                chat_title=row["chat_title"],
                format_type=MicroTestFormat(row["format_type"]),
                message_id=row["message_id"],
                sent_text=row["sent_text"],
                is_alive=bool(row["is_alive"]),
                replies_count=row["replies_count"],
                thread_depth=row["thread_depth"],
                admin_reaction=row["admin_reaction"],
                sent_at=datetime.fromisoformat(row["sent_at"]) if row["sent_at"] else None,
                checked_at=datetime.fromisoformat(row["checked_at"]) if row["checked_at"] else None,
            )

    async def get_micro_test(self, test_id: int) -> Optional[MicroTest]:
        return await asyncio.to_thread(self._sync_get_micro_test, test_id)

    def _sync_update_micro_test_telemetry(
        self,
        test_id: int,
        is_alive: bool,
        replies_count: int,
        thread_depth: int,
        admin_reaction: Optional[str],
    ) -> bool:
        query = """
        UPDATE micro_tests
        SET is_alive = ?, replies_count = ?, thread_depth = ?,
            admin_reaction = ?, checked_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                query,
                (1 if is_alive else 0, replies_count, thread_depth, admin_reaction, test_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    async def update_micro_test_telemetry(
        self,
        test_id: int,
        is_alive: bool,
        replies_count: int,
        thread_depth: int,
        admin_reaction: Optional[str] = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._sync_update_micro_test_telemetry,
            test_id,
            is_alive,
            replies_count,
            thread_depth,
            admin_reaction,
        )

    def _sync_get_micro_tests(
        self, chat_id: Optional[str], limit: int
    ) -> List[MicroTest]:
        where_clause = "WHERE chat_id = ?" if chat_id else ""
        params = (chat_id, limit) if chat_id else (limit,)
        query = (
            "SELECT id, chat_id, chat_title, format_type, message_id, sent_text, "
            "is_alive, replies_count, thread_depth, admin_reaction, sent_at, checked_at "
            f"FROM micro_tests {where_clause} ORDER BY sent_at DESC LIMIT ?"
        )
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                MicroTest(
                    id=row["id"],
                    chat_id=row["chat_id"],
                    chat_title=row["chat_title"],
                    format_type=MicroTestFormat(row["format_type"]),
                    message_id=row["message_id"],
                    sent_text=row["sent_text"],
                    is_alive=bool(row["is_alive"]),
                    replies_count=row["replies_count"],
                    thread_depth=row["thread_depth"],
                    admin_reaction=row["admin_reaction"],
                    sent_at=datetime.fromisoformat(row["sent_at"]) if row["sent_at"] else None,
                    checked_at=datetime.fromisoformat(row["checked_at"]) if row["checked_at"] else None,
                )
                for row in rows
            ]

    async def get_micro_tests(
        self, chat_id: Optional[str] = None, limit: int = 50
    ) -> List[MicroTest]:
        return await asyncio.to_thread(self._sync_get_micro_tests, chat_id, limit)

    def _sync_import_channel_audits(self, audits_db_path: Path) -> int:
        if not audits_db_path.exists():
            logger.warning("Source audits database not found at %s", audits_db_path)
            return 0

        source_conn = sqlite3.connect(str(audits_db_path))
        source_conn.row_factory = sqlite3.Row
        query = (
            "SELECT channel_id, username, title, fit_whale_funding, fit_retail_spot, "
            "best_entry_angle, pain_points, typical_context FROM channel_audits"
        )
        try:
            rows = source_conn.execute(query).fetchall()
        except Exception as e:
            logger.error("Failed to query source audits.db: %s", e)
            return 0
        finally:
            source_conn.close()

        count = len(rows)
        logger.info("Imported %d channel audits reference from %s", count, audits_db_path)
        return count

    async def import_channel_audits(self, audits_db_path: Optional[Path] = None) -> int:
        target_path = audits_db_path or (settings.base_dir.parent / "tg-channel-auditor" / "data" / "audits.db")
        return await asyncio.to_thread(self._sync_import_channel_audits, target_path)
