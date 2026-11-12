import pytest

from src.crm.pipeline_tracker import PipelineTracker
from src.storage.models import (
    PipelineType,
    DialogueStatus,
    FastTrackStage,
)


@pytest.mark.asyncio
async def test_pipeline_progression(temp_repo):
    tracker = PipelineTracker(temp_repo)
    user_id = 9991

    # Stage 1: In_DM
    await tracker.advance_stage(user_id, FastTrackStage.IN_DM, PipelineType.FAST_TRACK, note="First touch")
    diag = await temp_repo.get_dialogue(user_id, PipelineType.FAST_TRACK)
    assert diag.last_stage_reached == "In_DM"
    assert diag.status == DialogueStatus.ACTIVE

    # Stage 2: Legend_&_Media
    await tracker.advance_stage(user_id, FastTrackStage.LEGEND_AND_MEDIA, PipelineType.FAST_TRACK, note="Sent iPhone video")
    diag = await temp_repo.get_dialogue(user_id, PipelineType.FAST_TRACK)
    assert diag.last_stage_reached == "Legend_&_Media"

    # Stage 3: Test_20
    await tracker.advance_stage(user_id, FastTrackStage.TEST_20, PipelineType.FAST_TRACK, note="Sent 20 USDT instruction")
    diag = await temp_repo.get_dialogue(user_id, PipelineType.FAST_TRACK)
    assert diag.last_stage_reached == "Test_20"

    # Stage 4: Passed
    await tracker.advance_stage(user_id, FastTrackStage.PASSED, PipelineType.FAST_TRACK, note="Confirmed +$0.75 profit")
    diag = await temp_repo.get_dialogue(user_id, PipelineType.FAST_TRACK)
    assert diag.last_stage_reached == "Passed"

    # Stage 5: Volume_500_2k
    await tracker.advance_stage(user_id, FastTrackStage.VOLUME_500_2K, PipelineType.FAST_TRACK, note="User agreed to $2,000")
    diag = await temp_repo.get_dialogue(user_id, PipelineType.FAST_TRACK)
    assert diag.last_stage_reached == "Volume_500_2k"

    # Stage 6: Closed (WON)
    await tracker.close_deal(user_id, PipelineType.FAST_TRACK, note="First full volume cycle done")
    diag = await temp_repo.get_dialogue(user_id, PipelineType.FAST_TRACK)
    assert diag.last_stage_reached == "Closed"
    assert diag.status == DialogueStatus.WON


@pytest.mark.asyncio
async def test_pipeline_drop_and_analytics(temp_repo):
    tracker = PipelineTracker(temp_repo)

    # Lead 1: Refused test
    await tracker.advance_stage(101, FastTrackStage.IN_DM, PipelineType.FAST_TRACK)
    await tracker.advance_stage(101, FastTrackStage.LEGEND_AND_MEDIA, PipelineType.FAST_TRACK)
    await tracker.drop_lead(101, "refused_test_20", PipelineType.FAST_TRACK, note="Scared of deposit")

    # Lead 2: Successfully closed
    await tracker.advance_stage(102, FastTrackStage.IN_DM, PipelineType.FAST_TRACK)
    await tracker.close_deal(102, PipelineType.FAST_TRACK)

    # Lead 3: Ghosted after test profit
    await tracker.advance_stage(103, FastTrackStage.IN_DM, PipelineType.FAST_TRACK)
    await tracker.advance_stage(103, FastTrackStage.TEST_20, PipelineType.FAST_TRACK)
    await tracker.advance_stage(103, FastTrackStage.PASSED, PipelineType.FAST_TRACK)
    await tracker.drop_lead(103, "vanished_after_profit", PipelineType.FAST_TRACK, note="Ghosted")

    analytics = await tracker.get_funnel_analytics(PipelineType.FAST_TRACK)
    assert analytics["total_dialogues"] == 3
    assert analytics["won"] == 1
    assert analytics["lost"] == 2
    assert analytics["win_rate_percent"] == 33.33
    assert analytics["drop_reasons"]["refused_test_20"] == 1
    assert analytics["drop_reasons"]["vanished_after_profit"] == 1
