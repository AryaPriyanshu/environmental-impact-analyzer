"""Consumer-friendly device discovery and universal identity inference.

Official environmental datasets commonly expose certification identifiers rather
than the product-family names people type.  This module keeps the source records
intact while adding a derived search layer for aliases, ranking, typo recovery,
and a transparent fallback for devices that do not yet have a catalog record.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher, get_close_matches
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

from .utils import CATEGORY_BASELINES


CATEGORY_SEARCH_ALIASES = {
    "Smartphone": "phone mobile smartphone handset android ios",
    "Laptop": "laptop notebook portable computer pc ultrabook",
    "Tablet": "tablet slate portable touchscreen",
    "Television": "tv television smart tv oled qled",
    "Headphones": "headphones headphone earbuds earphones headset buds",
    "Smartwatch": "smartwatch smart watch wearable fitness watch",
    "Monitor": "monitor display screen computer display",
    "Desktop": "desktop computer workstation tower all in one pc",
    "Printer / scanner": "printer scanner multifunction copier",
    "Speaker": "speaker smart speaker audio",
    "Camera": "camera digital camera mirrorless dslr action camera",
    "Game console": "game console gaming handheld console",
    "Router / network": "router network wifi wi fi mesh access point switch",
    "Streaming device": "streaming device media player tv stick set top box",
    "Spatial computer": "spatial computer vr virtual reality mixed reality headset",
    "E-reader": "ereader e reader electronic paper ebook reader kindle remarkable",
    "Other": "gadget electronic device accessory",
}


# Family names identify a manufacturer even when the brand is omitted.
MANUFACTURER_SIGNALS: tuple[tuple[str, str], ...] = (
    (r"\b(?:apple|iphone|ipad|macbook|imac|airpods?|homepod|apple watch|vision pro)\b", "Apple"),
    (r"\b(?:samsung|galaxy|odyssey|smart monitor)\b", "Samsung"),
    (r"\b(?:google|pixel|chromecast|nest hub)\b", "Google"),
    (r"\b(?:microsoft|surface|xbox)\b", "Microsoft"),
    (r"\b(?:lenovo|thinkpad|ideapad|yoga|legion)\b", "Lenovo"),
    (r"\b(?:dell|alienware|inspiron|latitude|precision)\b|\bxps\b", "Dell"),
    (r"\b(?:hp|hewlett packard|spectre|pavilion|elitebook|probook|omen)\b", "HP"),
    (r"\b(?:asus|zenbook|vivobook|proart)\b|\brog\b", "ASUS"),
    (r"\b(?:acer|aspire|swift|travelmate|predator)\b", "Acer"),
    (r"\b(?:sony|playstation|bravia|xperia)\b", "Sony"),
    (r"\b(?:lg|gram|ultragear)\b", "LG"),
    (r"\b(?:oneplus|one plus)\b", "OnePlus"),
    (r"\b(?:xiaomi|redmi|poco)\b", "Xiaomi"),
    (r"\b(?:motorola|moto)\b", "Motorola"),
    (r"\b(?:huawei|matebook)\b", "Huawei"),
    (r"\b(?:fairphone)\b", "Fairphone"),
    (r"\b(?:nintendo|switch)\b", "Nintendo"),
    (r"\b(?:amazon|kindle|fire tv|echo)\b", "Amazon"),
    (r"\b(?:meta|oculus|quest)\b", "Meta"),
    (r"\b(?:bose)\b", "Bose"),
    (r"\b(?:jbl)\b", "JBL"),
)


# Ordered from specific to broad so, for example, Galaxy Tab is a tablet and
# Apple TV is a streaming device rather than a television.
CATEGORY_SIGNALS: tuple[tuple[str, str], ...] = (
    (r"\b(?:kindle|e[- ]?reader|ebook reader|remarkable)\b", "E-reader"),
    (r"\b(?:galaxy tab|ipad|tablet)\b", "Tablet"),
    (
        r"\b(?:airpods?|galaxy buds?|pixel buds?|earbuds?|earphones?|headphones?|headset"
        r"|(?:wh|wf) ?(?:1000xm\d|ch\d{3}[a-z]*))\b",
        "Headphones",
    ),
    (r"\b(?:apple watch|galaxy watch|pixel watch|smart ?watch|fitbit|fitness band|wearable)\b", "Smartwatch"),
    (r"\b(?:apple tv|chromecast|roku|fire tv|streaming (?:stick|device)|media player|set top box)\b", "Streaming device"),
    (r"\b(?:vision pro|meta quest|oculus|virtual reality|mixed reality|vr headset|spatial computer)\b", "Spatial computer"),
    (r"\b(?:playstation|xbox|nintendo switch|steam deck|game console|gaming handheld)\b", "Game console"),
    (r"\b(?:macbook|thinkpad|ideapad|chromebook|notebook|ultrabook|laptop|surface laptop|galaxy book|matebook)\b", "Laptop"),
    (r"\b(?:iphone|galaxy (?:[aszfmn]\d+|note ?\d+|fold ?\d+|flip ?\d+)|pixel (?:\d+|fold)|smartphone|mobile phone|cell phone|handset|xperia|fairphone|oneplus)\b", "Smartphone"),
    (r"\b(?:imac|mac mini|mac studio|mac pro|desktop|workstation|tower pc|all in one)\b", "Desktop"),
    (r"\b(?:monitor|computer display|ultragear|odyssey g\d)\b", "Monitor"),
    (r"\b(?:smart tv|television|bravia|oled tv|qled tv|\d{2,3}[- ]?inch tv)\b", "Television"),
    (r"\b(?:router|wi ?fi|mesh network|access point|network switch|modem)\b", "Router / network"),
    (r"\b(?:printer|scanner|multifunction|copier)\b", "Printer / scanner"),
    (r"\b(?:homepod|smart speaker|bluetooth speaker|soundbar|speaker)\b", "Speaker"),
    (r"\b(?:camera|mirrorless|dslr|gopro|action cam|webcam)\b", "Camera"),
    (r"\b(?:phone|mobile)\b", "Smartphone"),
    (r"\b(?:tv|oled|qled)\b", "Television"),
)


POPULAR_SEARCHES = (
    "iPhone",
    "MacBook Air",
    "Samsung Galaxy",
    "Galaxy Tab",
    "Google Pixel",
    "ThinkPad",
    "Apple Watch",
    "AirPods",
)

DEFAULT_IDENTITY_CATALOG = Path(__file__).resolve().parents[1] / "data" / "consumer_identities.csv"


def normalize_search_text(value: Any) -> str:
    """Return a punctuation-insensitive, human-search-friendly representation."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold()
    # ``+`` carries product meaning (for example Galaxy S25+); preserve it as
    # a word instead of collapsing the Plus variant into the base model.
    text = text.replace("+", " plus ")
    text = re.sub(r"\bmac\s+book\b", "macbook", text)
    text = re.sub(r"\bi\s+phone\b", "iphone", text)
    text = re.sub(r"\bi\s+pad\b", "ipad", text)
    text = re.sub(r"\bair\s+pods?\b", "airpods", text)
    text = re.sub(r"\bgalaxybook\b", "galaxy book", text)
    text = re.sub(r"\bplay\s+station\b", "playstation", text)
    text = re.sub(r"\bsmart\s+phone\b", "smartphone", text)
    text = re.sub(r"\bwi[ -]?fi\b", "wifi", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _without_configuration(value: str) -> str:
    text = re.sub(r"\b\d+(?:\.\d+)?\s*(?:gb|tb)\b", " ", value, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:wi[ -]?fi|cellular|ethernet)\b", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" -_/(),")


def aliases_for_record(record: Any) -> str:
    """Build derived aliases without modifying or overstating source evidence."""
    getter = record.get if hasattr(record, "get") else lambda key, default="": default
    manufacturer = str(getter("manufacturer", "") or "")
    name = str(getter("name", "") or "")
    model = str(getter("model_number", "") or "")
    category = str(getter("category", "Other") or "Other")
    additional = str(getter("additional_model_information", "") or "")
    upc_codes = str(getter("upc_codes", "") or "")
    official_identifier = str(getter("energy_star_model_identifier", "") or "")
    aliases = {
        manufacturer,
        name,
        model,
        f"{manufacturer} {name}",
        f"{manufacturer} {model}",
        _without_configuration(name),
        CATEGORY_SEARCH_ALIASES.get(category, CATEGORY_SEARCH_ALIASES["Other"]),
        additional,
        upc_codes,
        official_identifier,
    }
    normal_name = normalize_search_text(name)
    normal_model = normalize_search_text(model)
    normal_manufacturer = normalize_search_text(manufacturer)
    if normal_manufacturer == "apple":
        if "macbook" in normal_name:
            aliases.update({"apple laptop", normal_name.replace("macbook", "mac book")})
        if "iphone" in normal_name:
            aliases.update({"apple phone", "ios phone"})
        if "ipad" in normal_name:
            aliases.add("apple tablet")
        if "watch" in normal_name:
            aliases.update({"apple smartwatch", "iwatch"})
        if "airpods" in normal_name:
            aliases.update({"apple earbuds", "apple earphones"})
    if normal_manufacturer == "samsung":
        if normal_model.startswith("np") or "galaxy book" in normal_name:
            aliases.update({"samsung galaxy book", "samsung laptop"})
        if category == "Tablet":
            aliases.update({"samsung galaxy tab", "samsung tablet"})
        elif category == "Smartphone":
            aliases.update({"samsung galaxy phone", "samsung android phone"})
        elif category == "Smartwatch":
            aliases.add("samsung galaxy watch")
        elif category == "Headphones":
            aliases.add("samsung galaxy buds")
        if "galaxy" in normal_name:
            aliases.add(f"samsung {normal_name}")
    if normal_manufacturer == "google" and "pixel" in normal_name:
        aliases.add("google phone")
    if normal_manufacturer in {"hp", "hewlett packard"}:
        aliases.update({"hp", "hewlett packard"})
    return " | ".join(sorted({normalize_search_text(alias) for alias in aliases if normalize_search_text(alias)}))


def apply_catalog_corrections(frame: pd.DataFrame) -> pd.DataFrame:
    """Correct clear source-taxonomy errors without inventing measurements.

    ENERGY STAR's computer feed includes tablets.  Older snapshots classified
    every portable computer as a laptop, which hid iPads and Pixel Tablets from
    tablet searches.  Only category-derived defaults are changed here; observed
    source measurements remain untouched.
    """
    corrected = frame.copy()
    identity = (
        corrected.get("manufacturer", pd.Series("", index=corrected.index)).fillna("").astype(str)
        + " "
        + corrected.get("name", pd.Series("", index=corrected.index)).fillna("").astype(str)
        + " "
        + corrected.get("raw_product_type", pd.Series("", index=corrected.index)).fillna("").astype(str)
    ).map(normalize_search_text)
    tablet_mask = corrected["category"].eq("Laptop") & identity.str.contains(
        r"\b(?:ipad|pixel tablet|galaxy tab|tablet|slate)\b", regex=True, na=False
    )
    if not tablet_mask.any():
        return corrected

    tablet = CATEGORY_BASELINES["Tablet"]
    corrected.loc[tablet_mask, "category"] = "Tablet"
    # These fields are category assumptions in the current v3 snapshot.
    corrected.loc[tablet_mask, "manufacturing_kg"] = tablet["manufacturing"]
    corrected.loc[tablet_mask, "lifespan_years"] = tablet["life"]
    corrected.loc[tablet_mask, "weight_kg"] = tablet["weight"]
    if "observed_energy" in corrected:
        estimated_energy = tablet_mask & ~corrected["observed_energy"].map(_truthy)
        corrected.loc[estimated_energy, "active_power_w"] = tablet["power"]
    if "observed_battery" in corrected:
        estimated_battery = tablet_mask & ~corrected["observed_battery"].map(_truthy)
        corrected.loc[estimated_battery, "battery_wh"] = tablet["battery"]
    return corrected


def merge_consumer_identities(frame: pd.DataFrame, path: Path = DEFAULT_IDENTITY_CATALOG) -> pd.DataFrame:
    """Append reviewed identity-only products that open lifecycle feeds omit.

    Supplemental rows prove only that the named product exists unless an
    observed field is explicitly present.  Missing lifecycle fields remain
    null and are supplied as visible category assumptions during assessment.
    """
    if not path.exists():
        return frame.copy()
    identities = pd.read_csv(path, low_memory=False)
    if identities.empty:
        return frame.copy()
    required = {"product_id", "manufacturer", "name", "model_number", "category", "source_name", "source_url"}
    missing = required.difference(identities.columns)
    if missing:
        raise ValueError(f"Consumer identity catalog is missing columns: {', '.join(sorted(missing))}")

    identities = identities.copy()
    boolean_fields = (
        "observed_energy",
        "observed_repairability",
        "observed_carbon",
        "observed_battery",
        "observed_durability",
        "observed_software_support",
    )
    for field in boolean_fields:
        if field not in identities:
            identities[field] = False
        identities[field] = identities[field].fillna(False).map(_truthy)
    defaults: dict[str, Any] = {
        "source_type": "manufacturer_identity",
        "source_license": "Manufacturer page; identity facts linked, source terms apply",
        "source_retrieved_at": "",
        "market_date": "",
        "certification_date": "",
        "markets": "Global / see source",
        "observed_field_count": 0,
        "annual_energy_kwh": None,
        "duplicate_group_size": 1,
        "is_primary_record": True,
        "evidence_source_count": 1,
        "freshness_status": "active",
        "data_quality": "Identity only",
    }
    for field, default in defaults.items():
        if field not in identities:
            identities[field] = default
        else:
            identities[field] = identities[field].where(identities[field].notna(), default)
    current_year = date.today().year
    market_year = pd.to_numeric(identities["market_date"].astype(str).str.extract(r"(\d{4})", expand=False), errors="coerce")
    identities["freshness_status"] = "active"
    identities.loc[market_year >= current_year - 2, "freshness_status"] = "recent"
    identities.loc[market_year < current_year - 5, "freshness_status"] = "legacy"
    observed_counts = pd.to_numeric(identities["observed_field_count"], errors="coerce").fillna(0)
    identities.loc[observed_counts.between(2, 3), "data_quality"] = "Moderate"
    identities.loc[observed_counts >= 4, "data_quality"] = "Strong"
    identities["canonical_manufacturer"] = identities["manufacturer"].map(normalize_search_text)
    identities["canonical_model"] = identities["model_number"].map(normalize_search_text)
    identities["entity_key"] = identities["product_id"].astype(str)

    existing_keys = {
        f"{normalize_search_text(manufacturer)}|{normalize_search_text(name)}|{category}"
        for manufacturer, name, category in zip(frame["manufacturer"], frame["name"], frame["category"])
    }
    identity_keys = [
        f"{normalize_search_text(manufacturer)}|{normalize_search_text(name)}|{category}"
        for manufacturer, name, category in zip(identities["manufacturer"], identities["name"], identities["category"])
    ]
    identities = identities[[key not in existing_keys for key in identity_keys]]
    if identities.empty:
        return frame.copy()
    all_columns = list(dict.fromkeys([*frame.columns, *identities.columns]))
    identity_rows = identities.reindex(columns=all_columns).dropna(axis=1, how="all")
    return pd.concat([frame.reindex(columns=all_columns), identity_rows], ignore_index=True)


def prepare_catalog_search(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach reusable derived search columns to a catalog frame."""
    prepared = apply_catalog_corrections(frame)
    if "search_aliases" not in prepared:
        prepared["search_aliases"] = [aliases_for_record(row) for _, row in prepared.iterrows()]
    else:
        missing = prepared["search_aliases"].isna() | prepared["search_aliases"].astype(str).str.strip().eq("")
        if missing.any():
            prepared.loc[missing, "search_aliases"] = [aliases_for_record(row) for _, row in prepared.loc[missing].iterrows()]
    for source, target in (
        ("manufacturer", "_search_manufacturer"),
        ("name", "_search_name"),
        ("model_number", "_search_model"),
        ("search_aliases", "_search_aliases"),
    ):
        prepared[target] = prepared[source].fillna("").map(normalize_search_text)
    prepared["_search_text"] = (
        prepared["_search_manufacturer"]
        + " "
        + prepared["_search_name"]
        + " "
        + prepared["_search_model"]
        + " "
        + prepared["_search_aliases"]
    ).str.replace(r"\s+", " ", regex=True)
    return prepared


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or (isinstance(value, float) and value != value):
        return False
    return str(value).strip().casefold() in {"true", "1", "yes", "y"}


def _fuzzy_ratio(query: str, name: str, model: str, aliases: str) -> float:
    choices = [name, model]
    # Derived aliases include broad category words (for example ``phone``),
    # which are useful for literal discovery but too permissive for typo
    # recovery.  Fuzzy matching therefore stays on identity fields only.
    phrase_score = max((SequenceMatcher(None, query, choice).ratio() for choice in choices if choice), default=0.0)
    query_tokens = [token for token in query.split() if not token.isdigit()]
    record_tokens = {token for token in " ".join(choices).split() if not token.isdigit()}
    token_score = 0.0
    if query_tokens and record_tokens:
        token_score = sum(max(SequenceMatcher(None, token, candidate).ratio() for candidate in record_tokens) for token in query_tokens) / len(query_tokens)
    return max(phrase_score, token_score * 0.92)


def search_catalog_frame(
    frame: pd.DataFrame,
    query: str = "",
    *,
    categories: Optional[Iterable[str]] = None,
    sources: Optional[Iterable[str]] = None,
    manufacturers: Optional[Iterable[str]] = None,
    primary_only: bool = False,
    limit: Optional[int] = None,
) -> pd.DataFrame:
    """Filter and rank records using names, identifiers, aliases, and typos."""
    prepared = frame if "_search_text" in frame else prepare_catalog_search(frame)
    result = prepared
    for column, selected in (("category", categories), ("source_name", sources)):
        selected_values = list(selected or [])
        if selected_values:
            result = result[result[column].isin(selected_values)]
    selected_manufacturers = {normalize_search_text(value) for value in (manufacturers or []) if normalize_search_text(value)}
    if selected_manufacturers:
        result = result[result["_search_manufacturer"].isin(selected_manufacturers)]
    if primary_only and "is_primary_record" in result:
        result = result[result["is_primary_record"].map(_truthy)]

    normalized_query = normalize_search_text(query)
    if not normalized_query:
        return result.head(limit) if limit else result

    terms = normalized_query.split()
    direct_mask = pd.Series(True, index=result.index)
    for term in terms:
        # Match the beginning of a normalized token.  This keeps useful prefix
        # search (``think`` -> ThinkPad) while preventing short terms such as
        # ``tab`` from matching the middle of unrelated words like portable.
        suffix = r"(?: |$)" if len(term) <= 2 else ""
        direct_mask &= result["_search_text"].str.contains(rf"(?:^| ){re.escape(term)}{suffix}", regex=True, na=False)
    direct = result[direct_mask].copy()
    if not direct.empty:
        direct["_search_rank"] = 100.0
        combined_name = direct["_search_manufacturer"] + " " + direct["_search_name"]
        combined_model = direct["_search_manufacturer"] + " " + direct["_search_model"]
        direct.loc[combined_name.eq(normalized_query) | combined_model.eq(normalized_query), "_search_rank"] += 130
        direct.loc[combined_name.str.startswith(normalized_query, na=False) | combined_model.str.startswith(normalized_query, na=False), "_search_rank"] += 80
        direct.loc[direct["_search_name"].eq(normalized_query), "_search_rank"] += 100
        direct.loc[direct["_search_model"].eq(normalized_query), "_search_rank"] += 95
        direct.loc[direct["_search_name"].str.startswith(normalized_query, na=False), "_search_rank"] += 60
        direct.loc[direct["_search_name"].str.contains(normalized_query, regex=False, na=False), "_search_rank"] += 45
        direct.loc[direct["_search_manufacturer"].eq(normalized_query), "_search_rank"] += 20
        inferred_manufacturer = normalize_search_text(infer_device_identity(normalized_query)["manufacturer"])
        if inferred_manufacturer and inferred_manufacturer != "unknown":
            direct.loc[direct["_search_manufacturer"].eq(inferred_manufacturer), "_search_rank"] += 70
        if "is_primary_record" in direct:
            direct["_search_rank"] += direct["is_primary_record"].map(_truthy).astype(int) * 5
        if "observed_field_count" in direct:
            direct["_search_rank"] += pd.to_numeric(direct["observed_field_count"], errors="coerce").fillna(0).clip(0, 8)
        direct = direct.sort_values(["_search_rank", "manufacturer", "name"], ascending=[False, True, True])

    target = max(limit or 50, 12)
    if direct.empty and len(normalized_query) >= 3:
        fuzzy_pool = result
        inferred_category = infer_device_identity(normalized_query)["category"]
        if inferred_category != "Other" and "category" in fuzzy_pool:
            fuzzy_pool = fuzzy_pool[fuzzy_pool["category"].eq(inferred_category)]
        if fuzzy_pool.empty:
            return result.iloc[0:0].copy()
        phrase_choices = pd.concat([fuzzy_pool["_search_name"], fuzzy_pool["_search_model"]]).dropna().astype(str).unique().tolist()
        close_phrases = set(get_close_matches(normalized_query, phrase_choices, n=max(target * 5, 40), cutoff=0.62))
        if not close_phrases:
            return result.iloc[0:0].copy()
        candidate_mask = fuzzy_pool["_search_name"].isin(close_phrases) | fuzzy_pool["_search_model"].isin(close_phrases)
        fuzzy_pool = fuzzy_pool[candidate_mask]
        required_qualifiers = {word for word in ("plus", "ultra", "pro", "max", "mini", "air", "fold", "flip") if word in terms}
        required_numbers = {word for word in terms if word.isdigit()}
        fuzzy_rows: list[tuple[Any, float]] = []
        for index, row in fuzzy_pool.iterrows():
            record_terms = set(f"{row['_search_name']} {row['_search_model']}".split())
            if required_qualifiers and not required_qualifiers.issubset(record_terms):
                continue
            if required_numbers and not required_numbers.issubset(record_terms):
                continue
            ratio = _fuzzy_ratio(normalized_query, row["_search_name"], row["_search_model"], row["_search_aliases"])
            if ratio >= 0.68:
                fuzzy_rows.append((index, ratio * 90))
        if fuzzy_rows:
            fuzzy = fuzzy_pool.loc[[index for index, _ in fuzzy_rows]].copy()
            fuzzy["_search_rank"] = [score for _, score in fuzzy_rows]
            if "is_primary_record" in fuzzy:
                fuzzy["_search_rank"] += fuzzy["is_primary_record"].map(_truthy).astype(int) * 5
            fuzzy = fuzzy.sort_values(["_search_rank", "manufacturer", "name"], ascending=[False, True, True])
            direct = fuzzy

    if direct.empty:
        return result.iloc[0:0].copy()
    direct = direct[~direct.index.duplicated(keep="first")]
    return direct.head(limit) if limit else direct


def diversify_ranked_results(frame: pd.DataFrame, limit: int) -> pd.DataFrame:
    """Preserve relevance while ensuring broad searches show each category."""
    if limit <= 0 or frame.empty:
        return frame.iloc[0:0].copy()
    if len(frame) <= limit or "category" not in frame:
        return frame.head(limit)
    buckets = {category: list(group.index) for category, group in frame.groupby("category", sort=False)}
    ordered_categories = list(buckets)
    chosen: list[Any] = []
    while len(chosen) < limit and ordered_categories:
        next_round: list[str] = []
        for category in ordered_categories:
            if buckets[category]:
                chosen.append(buckets[category].pop(0))
                if len(chosen) == limit:
                    break
            if buckets[category]:
                next_round.append(category)
        ordered_categories = next_round
    return frame.loc[chosen]


def infer_device_identity(query: str) -> dict[str, str]:
    """Infer a conservative manufacturer/category for an unlisted device name."""
    normalized = normalize_search_text(query)
    manufacturer = "Unknown"
    category = "Other"
    for pattern, candidate in MANUFACTURER_SIGNALS:
        if re.search(pattern, normalized):
            manufacturer = candidate
            break
    for pattern, candidate in CATEGORY_SIGNALS:
        if re.search(pattern, normalized):
            category = candidate
            break
    signals = int(manufacturer != "Unknown") + int(category != "Other")
    confidence = "High" if signals == 2 else "Moderate" if signals == 1 else "Low"
    return {
        "name": str(query or "Unlisted gadget").strip() or "Unlisted gadget",
        "manufacturer": manufacturer,
        "category": category,
        "identity_confidence": confidence,
    }
