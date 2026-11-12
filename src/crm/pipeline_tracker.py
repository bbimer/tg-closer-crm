import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Union

from src.storage.models import (
    CrmDialogue,
    PipelineType,
    DialogueStatus,
    FastTrackStage,
    DeltaNeutralStage,
    FAST_TRACK_STAGES_ORDER,
    DELTA_NEUTRAL_STAGES_ORDER,
)
from src.storage.repository import Repository

logger = logging.getLogger(__name__)


# Standardized Drop Reasons based on Closer Playbook
VALID_DROP_REASONS = [
    "no_reply_to_legend",      # Stopped responding during legend / media intro
    "refused_test_20",         # Hesitated or refused $20 verification test
    "failed_test",             # Technical error during test / wrong network / order failed
    "insufficient_capital",    # No $1k-$2k working capital after test
    "skeptical_of_nodes",      # Questioned Asian nodes or insisted on AdsPower/emulators
    "vanished_after_profit",   # Ghosted after seeing +$0.75 profit on test
    "scam_accusation",         # Accused operator of scam / refused without discussion
    "unresponsive_cold",       # Never responded to the initial DM
    "other",                   # Manual reason
]


class PipelineTracker:
    """
    Manages lead progression through conversion funnels,
    records step-by-step audit trails, and calculates conversion metrics.
    """

    def __init__(self, repository: Repository):
        self.repo = repository

    async def advance_stage(
        self,
        lead_user_id: int,
        target_stage: Union[FastTrackStage, DeltaNeutralStage, str],
        pipeline_type: PipelineType = PipelineType.FAST_TRACK,
        note: str = "",
    ) -> bool:
        """
        Advances a lead to the next stage in the pipeline.
        Records the step in the dialogue's transition history.
        """
        stage_str = target_stage.value if hasattr(target_stage, "value") else str(target_stage)

        # Validate stage belonging to pipeline
        if pipeline_type == PipelineType.FAST_TRACK:
            valid_stages = [s.value for s in FAST_TRACK_STAGES_ORDER] + ["Lost"]
        else:
            valid_stages = [s.value for s in DELTA_NEUTRAL_STAGES_ORDER] + ["Lost"]

        if stage_str not in valid_stages:
            raise ValueError(
                f"Stage '{stage_str}' is invalid for pipeline '{pipeline_type.value}'. "
                f"Allowed: {valid_stages}"
            )

        dialogue = await self.repo.get_dialogue(lead_user_id, pipeline_type)
        if not dialogue:
            # Create new dialogue entry if doesn't exist yet
            dialogue = CrmDialogue(
                lead_user_id=lead_user_id,
                pipeline_type=pipeline_type,
                last_stage_reached=stage_str,
                status=DialogueStatus.ACTIVE if stage_str != "Closed" else DialogueStatus.WON,
                notes=note,
                history_json="[]",
            )
            await self.repo.upsert_dialogue(dialogue)

        # Parse existing history and append new event
        try:
            history = json.loads(dialogue.history_json)
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []

        history.append({
            "stage": stage_str,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "note": note,
        })

        is_won = (stage_str == "Closed")
        new_status = DialogueStatus.WON if is_won else DialogueStatus.ACTIVE

        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
        success = await self.repo.update_dialogue_stage(
            lead_user_id=lead_user_id,
            new_stage=stage_str,
            pipeline_type=pipeline_type,
            drop_reason=None if not is_won else dialogue.drop_reason,
            status=new_status,
            notes=f"{dialogue.notes}\n[{now_str}] -> {stage_str}: {note}".strip(),
        )

        logger.info(
            "Lead %d advanced to '%s' in %s (Won=%s)",
            lead_user_id,
            stage_str,
            pipeline_type.value,
            is_won,
        )
        return success

    async def drop_lead(
        self,
        lead_user_id: int,
        drop_reason: str,
        pipeline_type: PipelineType = PipelineType.FAST_TRACK,
        note: str = "",
    ) -> bool:
        """Marks a lead as Lost and records the specific bottleneck/reason."""
        dialogue = await self.repo.get_dialogue(lead_user_id, pipeline_type)
        if not dialogue:
            logger.warning("Cannot drop nonexistent dialogue for lead %d", lead_user_id)
            return False

        try:
            history = json.loads(dialogue.history_json)
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []

        history.append({
            "stage": "Lost",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "drop_reason": drop_reason,
            "note": note,
        })

        success = await self.repo.update_dialogue_stage(
            lead_user_id=lead_user_id,
            new_stage="Lost",
            pipeline_type=pipeline_type,
            drop_reason=drop_reason,
            status=DialogueStatus.LOST,
            notes=f"{dialogue.notes}\n[LOST: {drop_reason}] {note}".strip(),
        )

        logger.info(
            "Lead %d marked as LOST in %s. Reason: %s",
            lead_user_id,
            pipeline_type.value,
            drop_reason,
        )
        return success

    async def close_deal(
        self,
        lead_user_id: int,
        pipeline_type: PipelineType = PipelineType.FAST_TRACK,
        note: str = "Deal successfully closed on working volume",
    ) -> bool:
        """Shortcut to close the deal as WON."""
        return await self.advance_stage(
            lead_user_id=lead_user_id,
            target_stage="Closed",
            pipeline_type=pipeline_type,
            note=note,
        )

    async def get_funnel_analytics(
        self, pipeline_type: PipelineType = PipelineType.FAST_TRACK
    ) -> Dict[str, Any]:
        """Calculates stage-by-stage drop-off and conversion rates."""
        stats = await self.repo.get_pipeline_stats(pipeline_type)
        stages_order = (
            FAST_TRACK_STAGES_ORDER
            if pipeline_type == PipelineType.FAST_TRACK
            else DELTA_NEUTRAL_STAGES_ORDER
        )

        stage_counts = stats["stages"]
        total_dialogues = stats["total_dialogues"]

        # Step conversions
        funnel_steps = []
        for i, stage in enumerate(stages_order):
            count = stage_counts.get(stage.value, 0)
            funnel_steps.append({
                "stage": stage.value,
                "count": count,
            })

        won_count = stats["status"].get(DialogueStatus.WON.value, 0)
        lost_count = stats["status"].get(DialogueStatus.LOST.value, 0)
        active_count = stats["status"].get(DialogueStatus.ACTIVE.value, 0)

        win_rate = (won_count / total_dialogues * 100) if total_dialogues > 0 else 0.0

        return {
            "pipeline_type": pipeline_type.value,
            "total_dialogues": total_dialogues,
            "active": active_count,
            "won": won_count,
            "lost": lost_count,
            "win_rate_percent": round(win_rate, 2),
            "funnel_steps": funnel_steps,
            "drop_reasons": stats["drop_reasons"],
        }
