from datetime import datetime, timezone
import pytest

from src.intel.pain_miner import PainMiner
from src.storage.models import (
    GroupMessage,
    LeadSegment,
    PipelineType,
)


@pytest.mark.asyncio
async def test_pain_miner_stop_hunting(temp_repo):
    miner = PainMiner(temp_repo)

    msg = GroupMessage(
        chat_id="111",
        chat_title="Whale Trading Club",
        message_id=10,
        sender_id=555,
        sender_username="trader555",
        sender_name="Dmitry",
        text="Опять этот чертов Bybit нарисовал вертолет и выбило стоп на 200 баксов!",
        date=datetime.now(timezone.utc),
    )

    lead = miner.analyze_message(msg)
    assert lead is not None
    assert lead.user_id == 555
    assert lead.matched_module == "M02_EXCHANGE_MM"
    assert lead.exchange_mentioned == "Bybit"
    assert lead.lead_segment == LeadSegment.CLEAN_RETAIL
    assert lead.funnel_recommendation == PipelineType.FAST_TRACK


@pytest.mark.asyncio
async def test_pain_miner_liquidation_and_cex_hate(temp_repo):
    miner = PainMiner(temp_repo)

    msg = GroupMessage(
        chat_id="111",
        chat_title="Futures Scalping",
        message_id=11,
        sender_id=666,
        sender_username="crypto_victim",
        sender_name="Alex",
        text="Бинанс помойка конченая, ликвидировало лонг на ровном месте",
        date=datetime.now(timezone.utc),
    )

    lead = miner.analyze_message(msg)
    assert lead is not None
    assert lead.user_id == 666
    assert lead.matched_module in ("M02_EXCHANGE_MM", "M03_DERIVATIVES_FUNDING")
    assert lead.exchange_mentioned == "Binance"


@pytest.mark.asyncio
async def test_pain_miner_whale_segmentation(temp_repo):
    miner = PainMiner(temp_repo)

    msg = GroupMessage(
        chat_id="222",
        chat_title="Institutional Desk",
        message_id=20,
        sender_id=777,
        sender_username="whale_sergey",
        sender_name="Sergey",
        text="Ищу нормальный дельта-нейтрал или Cash & Carry на объем $100k, на споте сидеть надоело",
        date=datetime.now(timezone.utc),
    )

    lead = miner.analyze_message(msg)
    assert lead is not None
    assert lead.user_id == 777
    assert lead.lead_segment == LeadSegment.WHALE_ARBITRAGE
    assert lead.deposit_est == "50k_plus"
    assert lead.funnel_recommendation == PipelineType.DELTA_NEUTRAL


@pytest.mark.asyncio
async def test_mine_chat_integration(temp_repo):
    miner = PainMiner(temp_repo)

    msgs = [
        GroupMessage(
            chat_id="chat_777",
            chat_title="Crypto Talk",
            message_id=101,
            sender_id=1001,
            sender_username="pavel",
            sender_name="Pavel",
            text="Сняли стопы опять на OKX, сбрили лонги",
            date=datetime.now(timezone.utc),
        ),
        GroupMessage(
            chat_id="chat_777",
            chat_title="Crypto Talk",
            message_id=102,
            sender_id=1002,
            sender_username="ivan",
            sender_name="Ivan",
            text="Просто привет всем, как погода?",
            date=datetime.now(timezone.utc),
        ),
    ]

    await temp_repo.save_group_messages(msgs)
    leads = await miner.mine_chat("chat_777")

    assert len(leads) == 1
    assert leads[0].user_id == 1001
    assert leads[0].pain_category == "stop_hunt"

    stored_leads = await temp_repo.get_leads(pain_category="stop_hunt")
    assert len(stored_leads) == 1


@pytest.mark.asyncio
async def test_pain_miner_filters_bot_alerts(temp_repo):
    miner = PainMiner(temp_repo)

    # 1. News bot alert
    bot_msg = GroupMessage(
        chat_id="111",
        chat_title="Futures Scalping",
        message_id=50,
        sender_id=888,
        sender_username="news_feed_bot",
        sender_name="NewsBot",
        text="🔥 #ETH Liquidated Short: $39M at $2,112 -",
        date=datetime.now(timezone.utc),
    )
    assert miner.analyze_message(bot_msg) is None

    # 2. Whale alert
    whale_alert = GroupMessage(
        chat_id="111",
        chat_title="Futures Scalping",
        message_id=51,
        sender_id=889,
        sender_username="whale_watcher",
        sender_name="WhaleAlert",
        text="Whale Alert: 5,000 BTC ($450M) transferred from Binance to Unknown",
        date=datetime.now(timezone.utc),
    )
    assert miner.analyze_message(whale_alert) is None

    # 3. Massive technical paste without 1st person
    spec_paste = GroupMessage(
        chat_id="111",
        chat_title="Futures Scalping",
        message_id=52,
        sender_id=890,
        sender_username="spec_writer",
        sender_name="Dev",
        text="1. Anti-Tilt Protection System\n" + ("Обычный Risk Manager защищает депозит от неправильного расчёта позиции. " * 15),
        date=datetime.now(timezone.utc),
    )
    assert miner.analyze_message(spec_paste) is None

    # 4. Genuine 1st person trader message with pain
    personal_msg = GroupMessage(
        chat_id="111",
        chat_title="Futures Scalping",
        message_id=53,
        sender_id=999,
        sender_username="willfalcon",
        sender_name="Falcon",
        text="У меня ещё хуже, ликвидировало лонг. Я решил на яйцах добавиться снизу.",
        date=datetime.now(timezone.utc),
    )
    lead = miner.analyze_message(personal_msg)
    assert lead is not None
    assert lead.user_id == 999
    assert lead.pain_category == "liquidation"
