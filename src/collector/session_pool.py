import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple, AsyncGenerator

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyDuplicatedError,
    SessionRevokedError,
    UserDeactivatedError,
    FloodWaitError,
)
from config.settings import settings

logger = logging.getLogger(__name__)


class SessionPool:
    """
    Manages a pool of Telegram sessions with rotation, cooldowns,
    and automatic handling of flood waits and deactivated accounts.
    """

    def __init__(
        self,
        sessions_dir: Optional[Path] = None,
        api_id: Optional[int] = None,
        api_hash: Optional[str] = None,
    ):
        self.sessions_dir = sessions_dir or settings.telegram_sessions_dir
        self.api_id = api_id or settings.telegram_api_id
        self.api_hash = api_hash or settings.telegram_api_hash

        self._sessions: Dict[str, Dict] = {}
        self._lock = asyncio.Lock()
        self._init_sessions_list()

    def _init_sessions_list(self) -> None:
        if not self.sessions_dir.exists():
            logger.warning("Sessions directory %s does not exist.", self.sessions_dir)
            return

        for file in sorted(self.sessions_dir.glob("*.session")):
            if "backup" in str(file).lower():
                continue
            session_name = file.stem
            self._sessions[session_name] = {
                "path": file,
                "client": None,
                "status": "available",  # available, in_use, cooldown, disabled
                "cooldown_until": 0.0,
                "disabled_reason": None,
            }
        logger.info("Loaded %d session(s) into SessionPool.", len(self._sessions))

    @property
    def total_sessions_count(self) -> int:
        return len(self._sessions)

    @property
    def active_sessions_count(self) -> int:
        return sum(1 for m in self._sessions.values() if m["status"] in ("available", "in_use"))

    async def get_client(self) -> Tuple[TelegramClient, str]:
        """
        Retrieves the next available, authorized TelegramClient.
        Tries sessions in round-robin / available order.
        """
        async with self._lock:
            now = time.time()

            # 1. Update cooldowns
            for meta in self._sessions.values():
                if meta["status"] == "cooldown" and now >= meta["cooldown_until"]:
                    meta["status"] = "available"

            # 2. Find available session
            for name, meta in self._sessions.items():
                if meta["status"] != "available":
                    continue

                if meta["client"] is None:
                    session_file_no_ext = str(meta["path"].with_suffix(""))
                    client = TelegramClient(
                        session_file_no_ext,
                        self.api_id,
                        self.api_hash,
                        connection_retries=3,
                        retry_delay=2,
                    )
                    meta["client"] = client

                client = meta["client"]
                try:
                    if not client.is_connected():
                        await client.connect()

                    if not await client.is_user_authorized():
                        meta["status"] = "disabled"
                        meta["disabled_reason"] = "Unauthorized / Expired session"
                        await client.disconnect()
                        continue

                    meta["status"] = "in_use"
                    return client, name

                except (AuthKeyDuplicatedError, SessionRevokedError, UserDeactivatedError) as e:
                    meta["status"] = "disabled"
                    meta["disabled_reason"] = f"Permanent error: {e.__class__.__name__}"
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    continue
                except Exception as e:
                    meta["status"] = "cooldown"
                    meta["cooldown_until"] = now + 30.0
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    continue

            # 3. Check if all sessions are in cooldown
            min_wait = float("inf")
            for meta in self._sessions.values():
                if meta["status"] == "cooldown":
                    wait_time = meta["cooldown_until"] - now
                    if 0 < wait_time < min_wait:
                        min_wait = wait_time

            if min_wait != float("inf"):
                raise RuntimeError(
                    f"All Telegram sessions are in cooldown. Next available in {min_wait:.1f}s."
                )
            raise RuntimeError(
                "No working, authorized Telegram sessions available in session pool."
            )

    async def release_client(
        self, session_name: str, flood_wait_seconds: int = 0
    ) -> None:
        """Returns client to pool, setting cooldown if FloodWait occurred."""
        async with self._lock:
            meta = self._sessions.get(session_name)
            if not meta or meta["status"] == "disabled":
                return

            if flood_wait_seconds > 0:
                meta["status"] = "cooldown"
                meta["cooldown_until"] = time.time() + (
                    flood_wait_seconds * settings.flood_wait_backoff_factor
                )
            else:
                meta["status"] = "available"

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[Tuple[TelegramClient, str], None]:
        """Context manager for acquiring and safely releasing a TelegramClient."""
        client, session_name = await self.get_client()
        flood_wait = 0
        try:
            yield client, session_name
        except FloodWaitError as e:
            flood_wait = e.seconds
            logger.warning(
                "FloodWaitError on session %s: %d seconds required", session_name, flood_wait
            )
            raise
        finally:
            await self.release_client(session_name, flood_wait_seconds=flood_wait)

    async def close_all(self) -> None:
        """Disconnects all client sessions."""
        async with self._lock:
            for meta in self._sessions.values():
                client = meta.get("client")
                if client and client.is_connected():
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                meta["client"] = None
                if meta["status"] == "in_use":
                    meta["status"] = "available"
