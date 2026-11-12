from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# -------------------------------------------------------------------------
# Enums and Constants
# -------------------------------------------------------------------------

class PipelineType(str, Enum):
    FAST_TRACK = "fast_track"
    DELTA_NEUTRAL = "delta_neutral"


class FastTrackStage(str, Enum):
    IN_DM = "In_DM"
    LEGEND_AND_MEDIA = "Legend_&_Media"
    TEST_20 = "Test_20"
    PASSED = "Passed"
    VOLUME_500_2K = "Volume_500_2k"
    CLOSED = "Closed"
    LOST = "Lost"


class DeltaNeutralStage(str, Enum):
    IN_DM = "In_DM"
    UNIT_ECON_BREAKDOWN = "Unit_Econ_Breakdown"
    RISK_FRAMEWORK_PROOF = "Risk_Framework_Proof"
    TECH_SESSION_BOOKED = "Tech_Session_Booked"
    CLOSED = "Closed"
    LOST = "Lost"


class DialogueStatus(str, Enum):
    ACTIVE = "active"
    WON = "won"
    LOST = "lost"


class LeadSegment(str, Enum):
    CLEAN_RETAIL = "clean_retail"
    WHALE_ARBITRAGE = "whale_arbitrage"


class MicroTestFormat(str, Enum):
    FORMAT_A = "Format_A"  # Cold question
    FORMAT_B = "Format_B"  # Targeted reply to pain
    FORMAT_C = "Format_C"  # Visual hook / scanner mention


# Ordered stage progressions for validation & tracking
FAST_TRACK_STAGES_ORDER = [
    FastTrackStage.IN_DM,
    FastTrackStage.LEGEND_AND_MEDIA,
    FastTrackStage.TEST_20,
    FastTrackStage.PASSED,
    FastTrackStage.VOLUME_500_2K,
    FastTrackStage.CLOSED,
]

DELTA_NEUTRAL_STAGES_ORDER = [
    DeltaNeutralStage.IN_DM,
    DeltaNeutralStage.UNIT_ECON_BREAKDOWN,
    DeltaNeutralStage.RISK_FRAMEWORK_PROOF,
    DeltaNeutralStage.TECH_SESSION_BOOKED,
    DeltaNeutralStage.CLOSED,
]


# -------------------------------------------------------------------------
# SQLite DDL Statements
# -------------------------------------------------------------------------

CREATE_GROUP_MESSAGES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS group_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    chat_title TEXT,
    message_id INTEGER NOT NULL,
    sender_id INTEGER,
    sender_username TEXT,
    sender_name TEXT,
    text TEXT,
    reply_to_msg_id INTEGER DEFAULT NULL,
    date TIMESTAMP NOT NULL,
    has_media BOOLEAN NOT NULL DEFAULT 0,
    raw_data TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_group_msgs_chat ON group_messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_group_msgs_sender ON group_messages(sender_id);
CREATE INDEX IF NOT EXISTS idx_group_msgs_reply ON group_messages(reply_to_msg_id);
CREATE INDEX IF NOT EXISTS idx_group_msgs_date ON group_messages(date);
"""

CREATE_CRM_DIALOGUES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS crm_dialogues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_user_id INTEGER NOT NULL,
    lead_username TEXT,
    lead_name TEXT,
    source_chat_id TEXT,
    pipeline_type TEXT NOT NULL DEFAULT 'fast_track',
    last_stage_reached TEXT NOT NULL DEFAULT 'In_DM',
    status TEXT NOT NULL DEFAULT 'active',
    drop_reason TEXT DEFAULT NULL,
    matched_module TEXT DEFAULT NULL,
    deposit_est TEXT DEFAULT '500_2000',
    messages_count INTEGER NOT NULL DEFAULT 0,
    last_message_at TIMESTAMP,
    notes TEXT NOT NULL DEFAULT '',
    history_json TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(lead_user_id, pipeline_type)
);

CREATE INDEX IF NOT EXISTS idx_crm_user_id ON crm_dialogues(lead_user_id);
CREATE INDEX IF NOT EXISTS idx_crm_pipeline ON crm_dialogues(pipeline_type);
CREATE INDEX IF NOT EXISTS idx_crm_stage ON crm_dialogues(last_stage_reached);
CREATE INDEX IF NOT EXISTS idx_crm_status ON crm_dialogues(status);
"""

CREATE_MICRO_TESTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS micro_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    chat_title TEXT,
    format_type TEXT NOT NULL,
    message_id INTEGER,
    sent_text TEXT NOT NULL,
    is_alive BOOLEAN NOT NULL DEFAULT 1,
    replies_count INTEGER NOT NULL DEFAULT 0,
    thread_depth INTEGER NOT NULL DEFAULT 0,
    admin_reaction TEXT DEFAULT NULL,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    checked_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_micro_chat ON micro_tests(chat_id);
CREATE INDEX IF NOT EXISTS idx_micro_format ON micro_tests(format_type);
CREATE INDEX IF NOT EXISTS idx_micro_alive ON micro_tests(is_alive);
"""

CREATE_LEADS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT,
    display_name TEXT,
    source_chat_id TEXT NOT NULL,
    source_chat_title TEXT,
    pain_category TEXT NOT NULL,
    matched_module TEXT NOT NULL DEFAULT 'M01_TOKENOMICS_MEV',
    deposit_est TEXT NOT NULL DEFAULT '500_2000',
    exchange_mentioned TEXT,
    quote_sample TEXT NOT NULL,
    lead_segment TEXT NOT NULL DEFAULT 'clean_retail',
    funnel_recommendation TEXT NOT NULL DEFAULT 'fast_track',
    status TEXT NOT NULL DEFAULT 'new',
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, source_chat_id, pain_category)
);

CREATE INDEX IF NOT EXISTS idx_leads_user ON leads(user_id);
CREATE INDEX IF NOT EXISTS idx_leads_pain ON leads(pain_category);
CREATE INDEX IF NOT EXISTS idx_leads_module ON leads(matched_module);
CREATE INDEX IF NOT EXISTS idx_leads_segment ON leads(lead_segment);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
"""

ALL_DDL_STATEMENTS = [
    CREATE_GROUP_MESSAGES_TABLE_SQL,
    CREATE_CRM_DIALOGUES_TABLE_SQL,
    CREATE_MICRO_TESTS_TABLE_SQL,
    CREATE_LEADS_TABLE_SQL,
]


# -------------------------------------------------------------------------
# Pydantic Domain Models
# -------------------------------------------------------------------------

class GroupMessage(BaseModel):
    id: Optional[int] = None
    chat_id: str
    chat_title: Optional[str] = None
    message_id: int
    sender_id: Optional[int] = None
    sender_username: Optional[str] = None
    sender_name: Optional[str] = None
    text: Optional[str] = None
    reply_to_msg_id: Optional[int] = None
    date: datetime
    has_media: bool = False
    raw_data: str = "{}"
    created_at: Optional[datetime] = None


class CrmDialogue(BaseModel):
    id: Optional[int] = None
    lead_user_id: int
    lead_username: Optional[str] = None
    lead_name: Optional[str] = None
    source_chat_id: Optional[str] = None
    pipeline_type: PipelineType = PipelineType.FAST_TRACK
    last_stage_reached: str = FastTrackStage.IN_DM.value
    status: DialogueStatus = DialogueStatus.ACTIVE
    drop_reason: Optional[str] = None
    matched_module: Optional[str] = None
    deposit_est: str = "500_2000"
    messages_count: int = 0
    last_message_at: Optional[datetime] = None
    notes: str = ""
    history_json: str = "[]"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MicroTest(BaseModel):
    id: Optional[int] = None
    chat_id: str
    chat_title: Optional[str] = None
    format_type: MicroTestFormat
    message_id: Optional[int] = None
    sent_text: str
    is_alive: bool = True
    replies_count: int = 0
    thread_depth: int = 0
    admin_reaction: Optional[str] = None
    sent_at: Optional[datetime] = None
    checked_at: Optional[datetime] = None


class LeadProspect(BaseModel):
    id: Optional[int] = None
    user_id: int
    username: Optional[str] = None
    display_name: Optional[str] = None
    source_chat_id: str
    source_chat_title: Optional[str] = None
    pain_category: str
    matched_module: str = "M01_TOKENOMICS_MEV"
    deposit_est: str = "500_2000"
    exchange_mentioned: Optional[str] = None
    quote_sample: str
    lead_segment: LeadSegment = LeadSegment.CLEAN_RETAIL
    funnel_recommendation: PipelineType = PipelineType.FAST_TRACK
    status: str = "new"  # new, contacted, in_progress, archived
    detected_at: Optional[datetime] = None
