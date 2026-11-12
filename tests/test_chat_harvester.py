from datetime import datetime, timezone
import pytest

from src.intel.chat_harvester import ChatHarvester
from src.storage.models import GroupMessage


def test_slang_extraction():
    msgs = [
        GroupMessage(
            chat_id="1",
            message_id=1,
            text="Снова поймал стоп на Bybit, опять вертолет на фьючерсах",
            date=datetime.now(timezone.utc),
        ),
        GroupMessage(
            chat_id="1",
            message_id=2,
            text="Ликвидация лонга на Bybit, арбитраж спредов спас бы",
            date=datetime.now(timezone.utc),
        ),
        GroupMessage(
            chat_id="1",
            message_id=3,
            text="Кто крутит азиатские ноды? Фандинг положительный",
            date=datetime.now(timezone.utc),
        ),
    ]

    slang = ChatHarvester.extract_slang(msgs)
    terms = [term for term, count in slang]

    assert "bybit" in terms
    assert any("стоп" in t for t in terms)
    assert any("ликвид" in t for t in terms)
