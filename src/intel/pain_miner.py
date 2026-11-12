import logging
import re
from typing import Dict, List, Optional, Tuple, Any

from src.storage.models import (
    GroupMessage,
    LeadProspect,
    LeadSegment,
    PipelineType,
)
from src.storage.repository import Repository

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# 7-Module Curriculum Pain Taxonomy
# -------------------------------------------------------------------------

MODULE_PATTERNS = {
    "M01_TOKENOMICS_MEV": [
        r"разлок[а-я]*",
        r"фонд[ыа-я]*\s+(?:дамп(?:ят|нул)|слива(?:ют|ет))",
        r"вестинг",
        r"клифф",
        r"token\s*unlock",
        r"low\s*float",
        r"стейкинг[\s-]*ловушк[а-я]*",
        r"депег",
        r"depeg",
        r"сэндвич[\s-]*бот[а-я]*",
        r"sandwich\s*bot",
        r"honeypot",
        r"approve[\s-]*скам",
    ],
    "M02_EXCHANGE_MM": [
        r"(?:bybit|бинанс|байбит|binance|okx)\s+(?:помойка|казино|скам|рисует|манипулирует)",
        r"маркет[\s-]*мейкер[а-я]*\s+(?:рису(?:ет|ют)|вынес|манипул|забрал)",
        r"скам[\s-]*свеч[а-я]*",
        r"чистое\s+казино",
        r"манипуляци[яий]",
        r"wash[\s-]*trading",
        r"gotbit",
        r"грид[\s-]*бот[а-я]*\s+(?:слил|минус)",
        r"grid\s*bot",
        r"uniswap\s*v3",
        r"impermanent\s*loss",
        r"\blvr\b",
        r"непостоянн[а-я]+\s+потер[ия]",
        r"пул[а-я]*\s+ликвидност[ией]",
        r"(?:выбил[оа-я]*|сбил[оа-я]*|снес[лоа-я]*)\s+стоп[а-я]*",
        r"стоп[а-я]*\s+(?:снял[оа-я]*|выбил[оа-я]*|зацепил[оа-я]*)",
        r"вертолет[а-я]*",
        r"сбрил[оа-я]*\s+(?:лонги|шорты|стопы)",
        r"stop\s*hunt",
    ],
    "M03_DERIVATIVES_FUNDING": [
        r"ликвид[а-я]*",
        r"маржин\s*колл",
        r"(?:слил|потерял|минус)\s+(?:депо|депозит|баланс|\$\d+)",
        r"\brekt\b",
        r"\bliquidat(?:ed|ion)\b",
        r"обнулил[оа-я]*\s+депо",
        r"фандинг\s+(?:сожрал|минусит|конский|высокий)",
        r"funding\s*rate",
        r"каскад\s+ликвидаци[йя]",
        r"\badl\b",
        r"авто[\s-]*делеверидж",
        r"плеч[оа-я]*\s+(?:20x|50x|100x|высокое)",
    ],
    "M04_ORDERBOOK_EXECUTION": [
        r"cscalp",
        r"tigertrade",
        r"стакан\s+(?:лаг(?:ает|нул)|завис|пустой)",
        r"спуфинг",
        r"spoofing",
        r"айсберг[\s-]*ордер",
        r"iceberg",
        r"(?:конск[а-я]+|огромн[а-я]+|бешен[а-я]+)\s+комисси[яий]",
        r"taker\s*fee",
        r"тейкер\s+комисси[яий]",
        r"проскальзывани[ея]",
        r"slippage",
        r"спред\s+сожрал",
        r"ордер\s+размазал[оа-я]*",
    ],
    "M05_ONCHAIN_BRIDGES": [
        r"(?:завис|застрял)\s+мост",
        r"bridge\s+(?:stuck|завис)",
        r"секвенсор\s+(?:лег|упал|встал)",
        r"sequencer\s+down",
        r"arbitrum\s+(?:лег|завис)",
        r"wrapped\s+токен",
        r"кроссчейн",
        r"cross-chain",
        r"транзакци[яий]\s+висит",
    ],
    "M06_RISK_COMPLIANCE": [
        r"115[\s-]*фз",
        r"(?:блок|заблок|холд)\s+карт[ыеа-я]*",
        r"aml\s*(?:риск|блок|проверка)",
        r"chainalysis",
        r"грязн[а-я]+\s+крипт[а-я]",
        r"(?:обрыв|пропал)\s+(?:интернет|связь|vps)",
        r"скам\s+бирж[ией]",
        r"ftx",
        r"мартингейл",
        r"удвоени[ея]\s+позы",
    ],
    "M07_QUANT_STACK": [
        r"индикатор[ыа-я]*\s+tradingview",
        r"tradingview\s+(?:врет|сливает)",
        r"(?:ошибка|бан)\s+429",
        r"http\s*429",
        r"rate\s*limit",
        r"раскорреляци[яий]",
        r"тильт",
        r"тильтанул",
        r"эмоци[яий]\s+слил",
        r"ручной\s+трейдинг\s+слив",
    ],
}

COMPILED_MODULE_PATTERNS = {
    mod: re.compile("|".join(patterns), re.IGNORECASE)
    for mod, patterns in MODULE_PATTERNS.items()
}

EXCHANGES = [
    ("Bybit", re.compile(r"\b(?:bybit|байбит)\b", re.IGNORECASE)),
    ("Binance", re.compile(r"\b(?:binance|бинанс)\b", re.IGNORECASE)),
    ("OKX", re.compile(r"\b(?:okx|окикс|окекс)\b", re.IGNORECASE)),
    ("Bitget", re.compile(r"\b(?:bitget|битгет)\b", re.IGNORECASE)),
    ("MEXC", re.compile(r"\b(?:mexc|мекс)\b", re.IGNORECASE)),
    ("Gate", re.compile(r"\b(?:gate\.io|гейт)\b", re.IGNORECASE)),
    ("KuCoin", re.compile(r"\b(?:kucoin|кукоин)\b", re.IGNORECASE)),
    ("BingX", re.compile(r"\b(?:bingx|бингх)\b", re.IGNORECASE)),
]

WHALE_KEYWORDS_REGEX = re.compile(
    r"(?:\$50[kк]|\$100[kк]|\$500[kк]|50\s*000\$|100\s*000\$|otc|институционал|дельта[\s-]*нейтрал|cash\s*&\s*carry)",
    re.IGNORECASE,
)


# -------------------------------------------------------------------------
# Bot / Automated News & Alert Patterns (Filter Out)
# -------------------------------------------------------------------------

BOT_ALERT_PATTERNS = [
    r"^(?:🔥|🚨|⚡|📢|📊)?\s*#?[A-Z0-9]+\s+(?:Liquidated|Liquidation|Ликвидаци)\s+(?:Short|Long|шорт|лонг)",
    r"\b(?:Whale\s*Alert|Binance\s*Futures\s*Alert|Crypto\s*Alert)\b",
    r"\$\d+(?:\.\d+)?(?:M|B|K|к|млн|млрд)\s+(?:at|по|ликвид)",
]
COMPILED_BOT_ALERTS = re.compile("|".join(BOT_ALERT_PATTERNS), re.IGNORECASE)

# First-person conversational markers (boosts genuine human messages)
FIRST_PERSON_PATTERNS = [
    r"\b(?:я|мне|меня|мой|моего|моя|мои|мной|мною)\b",
    r"\b(?:добавился|зашел|заходил|выбило|выбивает|слил|сливаю|потерял|сижу|торгую|взял|закрыл|закрылся)\b",
    r"\b(?:лагает|завис|тильтанул|тильтую|подскажите|почему|кто|подскажет)\b",
]
COMPILED_FIRST_PERSON = re.compile("|".join(FIRST_PERSON_PATTERNS), re.IGNORECASE)


class PainMiner:
    """
    Scans raw chat messages to identify users experiencing acute trading pain points,
    maps them to the 7-day curriculum modules (M01-M07), and estimates their capital.
    """

    def __init__(self, repository: Repository):
        self.repo = repository

    def estimate_deposit(self, text: str, is_whale: bool) -> str:
        """Estimates deposit bracket from text mentions or defaults."""
        if is_whale:
            return "50k_plus"

        match = re.search(r"(\d+)\s*(?:к|\$|k|usd|usdt|долларов|баксов)", text, re.IGNORECASE)
        if match:
            try:
                num = int(match.group(1))
                if num >= 50:
                    return "50k_plus"
                if num >= 10:
                    return "10k_50k"
                if num >= 2:
                    return "2000_5000"
                if num >= 1:
                    return "1000_2000"
                return "500_1000"
            except ValueError:
                pass

        return "500_2000"

    def analyze_message(self, message: GroupMessage) -> Optional[LeadProspect]:
        """Analyzes a single message and returns a LeadProspect if any module pattern matches."""
        if not message.text or len(message.text.strip()) < 10:
            return None

        if not message.sender_id:
            return None

        # Exclude bot accounts
        if message.sender_username and message.sender_username.lower().endswith("bot"):
            return None

        text = message.text

        # Exclude automated alerts, liquidation bot posts, and whale alert feeds
        if COMPILED_BOT_ALERTS.search(text):
            return None

        # Exclude long articles, copy-pastes, or documentation dumps unless they have personal first-person text
        if len(text.strip()) > 600 and not COMPILED_FIRST_PERSON.search(text):
            return None

        # Detect matching module
        detected_module = None
        for mod_name, pattern in COMPILED_MODULE_PATTERNS.items():
            if pattern.search(text):
                detected_module = mod_name
                break

        # Also check whale inquiry fallback
        is_whale = bool(WHALE_KEYWORDS_REGEX.search(text))
        if not detected_module:
            if is_whale:
                detected_module = "M03_DERIVATIVES_FUNDING"
            else:
                return None

        # Derive subcategory from text
        sub_category = detected_module.lower()
        if "стоп" in text.lower() or "вертолет" in text.lower():
            sub_category = "stop_hunt"
        elif "ликвид" in text.lower() or "rekt" in text.lower():
            sub_category = "liquidation"
        elif "стакан" in text.lower() or "cscalp" in text.lower() or "проскальз" in text.lower():
            sub_category = "orderbook_lag"
        elif "мост" in text.lower() or "bridge" in text.lower():
            sub_category = "stuck_bridge"
        elif "разлок" in text.lower() or "вестинг" in text.lower():
            sub_category = "token_unlocks"

        # Detect exchange mention
        detected_exchange = None
        for exchange_name, pattern in EXCHANGES:
            if pattern.search(text):
                detected_exchange = exchange_name
                break

        lead_segment = LeadSegment.WHALE_ARBITRAGE if is_whale else LeadSegment.CLEAN_RETAIL
        funnel_recommendation = (
            PipelineType.DELTA_NEUTRAL if is_whale else PipelineType.FAST_TRACK
        )

        deposit_est = self.estimate_deposit(text, is_whale)

        # Truncate quote sample if too long
        quote_sample = text.strip()
        if len(quote_sample) > 280:
            quote_sample = quote_sample[:277] + "..."

        return LeadProspect(
            user_id=message.sender_id,
            username=message.sender_username,
            display_name=message.sender_name,
            source_chat_id=message.chat_id,
            source_chat_title=message.chat_title,
            pain_category=sub_category,
            matched_module=detected_module,
            deposit_est=deposit_est,
            exchange_mentioned=detected_exchange,
            quote_sample=quote_sample,
            lead_segment=lead_segment,
            funnel_recommendation=funnel_recommendation,
            status="new",
        )

    async def mine_chat(
        self, chat_id: str, limit: int = 1000
    ) -> List[LeadProspect]:
        """Loads messages from repository for a given chat, extracts qualifying leads."""
        logger.info("Mining pains across M01-M07 for chat_id '%s' (limit: %d)", chat_id, limit)
        messages = await self.repo.get_messages_by_chat(chat_id, limit=limit)

        leads: List[LeadProspect] = []
        seen_keys = set()

        for msg in messages:
            prospect = self.analyze_message(msg)
            if prospect:
                key = (prospect.user_id, prospect.matched_module)
                if key not in seen_keys:
                    seen_keys.add(key)
                    leads.append(prospect)

        saved_count = await self.repo.save_leads(leads)
        logger.info(
            "Mining complete for chat '%s': found %d leads, inserted %d new",
            chat_id,
            len(leads),
            saved_count,
        )
        return leads
