from datetime import datetime, timezone
import pytest

from src.storage.models import (
    GroupMessage,
    CrmDialogue,
    MicroTest,
    LeadProspect,
    PipelineType,
    DialogueStatus,
    FastTrackStage,
    LeadSegment,
    MicroTestFormat,
)


@pytest.mark.asyncio
async def test_group_messages_crud(temp_repo):
    msgs = [
        GroupMessage(
            chat_id="123456",
            chat_title="Test Chat",
            message_id=1,
            sender_id=999,
            sender_username="trader_bob",
            sender_name="Bob",
            text="Again bybit scam wick",
            reply_to_msg_id=None,
            date=datetime.now(timezone.utc),
            has_media=False,
        ),
        GroupMessage(
            chat_id="123456",
            chat_title="Test Chat",
            message_id=2,
            sender_id=888,
            sender_username="trader_alice",
            sender_name="Alice",
            text="Same here, stopped out",
            reply_to_msg_id=1,
            date=datetime.now(timezone.utc),
            has_media=False,
        ),
        GroupMessage(
            chat_id="123456",
            chat_title="Test Chat",
            message_id=3,
            sender_id=777,
            sender_username="trader_charlie",
            sender_name="Charlie",
            text="Liquidation happened",
            reply_to_msg_id=1,
            date=datetime.now(timezone.utc),
            has_media=False,
        ),
    ]

    count = await temp_repo.save_group_messages(msgs)
    assert count == 3

    # Duplicate insert test (should ignore duplicates)
    dup_count = await temp_repo.save_group_messages([msgs[0]])
    assert dup_count == 0

    fetched = await temp_repo.get_messages_by_chat("123456")
    assert len(fetched) == 3

    # Hot threads check
    hot = await temp_repo.get_hot_threads("123456", min_replies=2)
    assert len(hot) == 1
    assert hot[0]["message_id"] == 1
    assert hot[0]["reply_count"] == 2

    # Active users check
    active = await temp_repo.get_active_users("123456")
    assert len(active) == 3

    # Reply tree ladder stitching check (100 -> 101 -> 102 -> 103)
    ladder_msgs = [
        GroupMessage(
            chat_id="chat_tree",
            chat_title="Tree Chat",
            message_id=100,
            sender_id=10,
            sender_username="author_a",
            sender_name="Alice",
            text="BTC dump incoming, cliff tomorrow on TIA",
            reply_to_msg_id=None,
            date=datetime.now(timezone.utc),
        ),
        GroupMessage(
            chat_id="chat_tree",
            chat_title="Tree Chat",
            message_id=101,
            sender_id=11,
            sender_username="author_b",
            sender_name="Bob",
            text="No way, funding is negative",
            reply_to_msg_id=100,
            date=datetime.now(timezone.utc),
        ),
        GroupMessage(
            chat_id="chat_tree",
            chat_title="Tree Chat",
            message_id=102,
            sender_id=12,
            sender_username="author_c",
            sender_name="Charlie",
            text="Funding means nothing when cliff is $900M",
            reply_to_msg_id=101,  # ladder reply to 101
            date=datetime.now(timezone.utc),
        ),
        GroupMessage(
            chat_id="chat_tree",
            chat_title="Tree Chat",
            message_id=103,
            sender_id=10,
            sender_username="author_a",
            sender_name="Alice",
            text="Exactly, Delphi showed 78% drop",
            reply_to_msg_id=102,  # ladder reply to 102
            date=datetime.now(timezone.utc),
        ),
    ]
    await temp_repo.save_group_messages(ladder_msgs)
    threads = await temp_repo.get_active_threads("chat_tree", hours=24, limit=5)
    assert len(threads) == 1
    assert threads[0]["root_msg_id"] == 100
    assert threads[0]["reply_count"] == 3
    assert "TIA" in threads[0]["tickers"] or "BTC" in threads[0]["tickers"]

    # Test get_thread_messages ladder
    exported_thread = await temp_repo.get_thread_messages("chat_tree", 103, max_messages=5)
    assert exported_thread["thread_root_id"] == 100
    assert exported_thread["target_msg_id"] == 103
    assert len(exported_thread["messages"]) == 4
    assert exported_thread["messages"][0]["msg_id"] == 100
    assert exported_thread["messages"][-1]["msg_id"] == 103

    # Test slang extraction
    slang = await temp_repo.get_chat_top_slang("chat_tree")
    assert isinstance(slang, list)


@pytest.mark.asyncio
async def test_crm_dialogues_lifecycle(temp_repo):
    dialogue = CrmDialogue(
        lead_user_id=1001,
        lead_username="artem_trader",
        lead_name="Artem",
        pipeline_type=PipelineType.FAST_TRACK,
        last_stage_reached=FastTrackStage.IN_DM.value,
        status=DialogueStatus.ACTIVE,
        messages_count=5,
        last_message_at=datetime.now(timezone.utc),
    )

    await temp_repo.upsert_dialogue(dialogue)

    fetched = await temp_repo.get_dialogue(1001, PipelineType.FAST_TRACK)
    assert fetched is not None
    assert fetched.lead_username == "artem_trader"
    assert fetched.last_stage_reached == FastTrackStage.IN_DM.value

    # Update stage
    updated = await temp_repo.update_dialogue_stage(
        lead_user_id=1001,
        new_stage=FastTrackStage.TEST_20.value,
        pipeline_type=PipelineType.FAST_TRACK,
        notes="Sent test instructions",
    )
    assert updated is True

    fetched_after = await temp_repo.get_dialogue(1001, PipelineType.FAST_TRACK)
    assert fetched_after.last_stage_reached == FastTrackStage.TEST_20.value
    assert "Sent test instructions" in fetched_after.notes


@pytest.mark.asyncio
async def test_micro_tests_telemetry(temp_repo):
    test_record = MicroTest(
        chat_id="999888",
        chat_title="Test Alpha Group",
        format_type=MicroTestFormat.FORMAT_A,
        message_id=42,
        sent_text="Anyone noticing Bybit ping?",
    )

    test_id = await temp_repo.save_micro_test(test_record)
    assert test_id > 0

    record = await temp_repo.get_micro_test(test_id)
    assert record.is_alive is True
    assert record.replies_count == 0

    # Update telemetry
    await temp_repo.update_micro_test_telemetry(
        test_id=test_id,
        is_alive=True,
        replies_count=4,
        thread_depth=2,
        admin_reaction="none",
    )

    updated_record = await temp_repo.get_micro_test(test_id)
    assert updated_record.replies_count == 4
    assert updated_record.thread_depth == 2
    assert updated_record.admin_reaction == "none"
