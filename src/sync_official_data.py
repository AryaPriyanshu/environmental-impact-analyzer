"""Build a versioned, provenance-rich gadget data snapshot from public sources.

Observed source fields and category assumptions remain separate.  A regulatory
record is evidence about the fields it publishes; it is never presented as a
complete lifecycle assessment unless the manufacturer source reports one.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import date, datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests

try:
    from .utils import CATEGORY_BASELINES
except ImportError:
    from utils import CATEGORY_BASELINES


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT = DATA_DIR / "official_gadgets.csv"
METADATA = DATA_DIR / "source_metadata.json"
REPAIR_PROFILES = DATA_DIR / "repair_profiles.csv"
GRID_OUTPUT = DATA_DIR / "grid_intensity.csv"
TIMEOUT = 120
RETRIEVED_AT = datetime.now(timezone.utc).isoformat()
USER_AGENT = "GadgetImpactAnalyser/3.0 (+https://github.com/AryaPriyanshu/environmental-impact-analyzer)"

ENERGY_STAR = {
    "rxdj-2c88": ("ENERGY STAR Computers V9.0", "Computer"),
    "qbg3-d468": ("ENERGY STAR Displays V8.0", "Monitor"),
    "pd96-rr3d": ("ENERGY STAR Televisions V9.x", "Television"),
    "t2v6-g4nf": ("ENERGY STAR Imaging Equipment V3.x", "Printer / scanner"),
    "n8cx-m62r": ("ENERGY STAR Large Network Equipment", "Router / network"),
}
EPREL_ENDPOINT = "https://eprel.ec.europa.eu/api/products/smartphonestablets20231669"
EPREL_PAGE = "https://eprel.ec.europa.eu/screen/product/smartphonestablets20231669"
REPAIR_DATASET_API = "https://www.data.gouv.fr/api/1/datasets/fichiers-consolides-des-donnees-respectant-le-schema-indice-de-reparabilite/"
REPAIR_PAGE = "https://www.data.gouv.fr/datasets/fichiers-consolides-des-donnees-respectant-le-schema-indice-de-reparabilite"
IFIXIT_CSV = "https://docs.google.com/spreadsheets/d/1R_egXm7iwR0isCt_UxcGtheFEwLj9SNhxC5DWnAqKIc/gviz/tq?tqx=out:csv"
IFIXIT_PAGE = "https://fr.ifixit.com/reparabilite/indices-smartphone"
OPEN_REPAIR_CSV = "https://raw.githubusercontent.com/openrepair/data/master/aggregated/202507/OpenRepairData_v0.3_aggregate_202507.csv"
OPEN_REPAIR_PAGE = "https://openrepair.org/open-data/downloads/"
GRID_CSV = "https://ourworldindata.org/grapher/electricity-mix.csv?v=1&csvType=full&useColumnShortNames=false&source=total&metric=carbon_intensity&frequency=annual"
GRID_PAGE = "https://ourworldindata.org/grapher/carbon-intensity-electricity"


# Entries are transcribed from linked product environmental reports or the
# product-footprint appendix in Apple's 2026 Environmental Progress Report.
# ``manufacturing_share`` is only used when the linked report publishes it.
MANUFACTURER_REPORTS = [
    {"manufacturer": "Samsung", "name": "Galaxy S26", "year": 2026, "category": "Smartphone", "total": 40.3, "manufacturing_share": 0.881, "url": "https://www.samsung.com/global/sustainability/landing_hub-file/AZzQUdPqFI0ALYOH/Galaxy_S26_Environmental_Report_EN.pdf"},
    {"manufacturer": "Samsung", "name": "Galaxy S25", "year": 2025, "category": "Smartphone", "total": 45.7, "manufacturing_share": 0.880, "url": "https://www.samsung.com/global/sustainability/landing_hub-file/AZUXQCVKIiwALYMV/Galaxy_S25_Environmental_Report_EN_2503.pdf"},
    {"manufacturer": "Samsung", "name": "Galaxy S25+", "year": 2025, "category": "Smartphone", "total": 46.2, "manufacturing_share": 0.876, "url": "https://www.samsung.com/global/sustainability/landing_hub-file/AZUXQLt6IkoALYMV/Galaxy_S25%2B_Environmental_Report_EN_2503.pdf"},
    {"manufacturer": "Samsung", "name": "Galaxy S25 Ultra", "year": 2025, "category": "Smartphone", "total": 48.9, "manufacturing_share": 0.875, "url": "https://www.samsung.com/global/sustainability/landing_hub-file/AZUXQdtKImgALYMV/Galaxy_S25_Ultra_Environmental_Report_EN.pdf"},
    {"manufacturer": "Samsung", "name": "Galaxy S24+", "year": 2024, "category": "Smartphone", "total": 54.8, "manufacturing_share": 0.848, "url": "https://www.samsung.com/global/sustainability/media/pdf/Galaxy_S24%2B_Environmental_Report_EN.pdf"},
    {"manufacturer": "Apple", "name": "iPhone 17 256GB", "year": 2025, "category": "Smartphone", "total": 55, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "iPhone 17 Pro 256GB", "year": 2025, "category": "Smartphone", "total": 64, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "iPhone 17 Pro Max 256GB", "year": 2025, "category": "Smartphone", "total": 67, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "iPhone 17e 256GB", "year": 2026, "category": "Smartphone", "total": 47, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "iPhone Air 256GB", "year": 2025, "category": "Smartphone", "total": 55, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "iPhone 16", "year": 2024, "category": "Smartphone", "total": 56, "url": "https://www.apple.com/ee/environment/pdf/products/iphone/iPhone_16_and_iPhone_16_Plus_PER_June2025.pdf"},
    {"manufacturer": "Apple", "name": "iPhone 16 Plus", "year": 2024, "category": "Smartphone", "total": 60, "url": "https://www.apple.com/ee/environment/pdf/products/iphone/iPhone_16_and_iPhone_16_Plus_PER_June2025.pdf"},
    {"manufacturer": "Apple", "name": "iPhone 15 Pro", "year": 2023, "category": "Smartphone", "total": 66, "url": "https://www.apple.com/co/environment/pdf/products/iphone/iPhone_15_Pro_and_iPhone_15_Pro_Max_Sept2023.pdf"},
    {"manufacturer": "Apple", "name": "iPhone 15 Pro Max", "year": 2023, "category": "Smartphone", "total": 75, "url": "https://www.apple.com/co/environment/pdf/products/iphone/iPhone_15_Pro_and_iPhone_15_Pro_Max_Sept2023.pdf"},
    {"manufacturer": "Apple", "name": "iPad Pro 13-inch M5 256GB", "year": 2025, "category": "Tablet", "total": 120, "url": "https://www.apple.com/hr/environment/pdf/products/ipad/iPad_Pro_11_and_13_inch_PER_Oct2025_EU.pdf"},
    {"manufacturer": "Apple", "name": "iPad Pro 11-inch M5 256GB", "year": 2025, "category": "Tablet", "total": 103, "url": "https://www.apple.com/hr/environment/pdf/products/ipad/iPad_Pro_11_and_13_inch_PER_Oct2025_EU.pdf"},
    {"manufacturer": "Apple", "name": "iPad Air 13-inch M4 128GB", "year": 2026, "category": "Tablet", "total": 89, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "iPad Air 11-inch M4 128GB", "year": 2026, "category": "Tablet", "total": 74, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "iPad A16 128GB", "year": 2025, "category": "Tablet", "total": 74, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "iPad mini A17 Pro 128GB", "year": 2024, "category": "Tablet", "total": 65, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "Apple Watch Ultra 3", "year": 2025, "category": "Smartwatch", "total": 11.0, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "Apple Watch Series 11", "year": 2025, "category": "Smartwatch", "total": 8.1, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "Apple Watch SE 3", "year": 2025, "category": "Smartwatch", "total": 8.2, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "MacBook Air 13-inch M5 512GB", "year": 2026, "category": "Laptop", "total": 119, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "MacBook Air 15-inch M5 512GB", "year": 2026, "category": "Laptop", "total": 145, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "MacBook Pro 14-inch M5 512GB", "year": 2025, "category": "Laptop", "total": 164, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "MacBook Pro 16-inch M5 Pro 1TB", "year": 2025, "category": "Laptop", "total": 280, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "Mac mini M4 256GB", "year": 2024, "category": "Desktop", "total": 32, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "iMac two-port 256GB", "year": 2024, "category": "Desktop", "total": 346, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "Mac Studio M4 Max 512GB", "year": 2025, "category": "Desktop", "total": 276, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "Mac Pro 1TB", "year": 2023, "category": "Desktop", "total": 1572, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "HomePod 2nd generation", "year": 2023, "category": "Speaker", "total": 92, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "HomePod mini", "year": 2020, "category": "Speaker", "total": 42, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "Apple TV 4K Wi-Fi 64GB", "year": 2022, "category": "Streaming device", "total": 43, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "Apple TV 4K Wi-Fi + Ethernet 128GB", "year": 2022, "category": "Streaming device", "total": 46, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "Apple Vision Pro", "year": 2024, "category": "Spatial computer", "total": 335, "url": "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"},
    {"manufacturer": "Apple", "name": "AirPods Pro 3", "year": 2025, "category": "Headphones", "total": 13, "url": "https://www.apple.com/cl/environment/pdf/products/airpods/Apple_AirPods_Pro_3_PER_Sept2025.pdf"},
]


OPEN_REPAIR_CATEGORY_MAP = {
    "mobile": "Smartphone",
    "tablet": "Tablet",
    "laptop": "Laptop",
    "desktop computer": "Desktop",
    "headphones": "Headphones",
    "hi-fi speaker": "Speaker",
    "games console": "Game console",
    "handheld entertainment device": "Game console",
    "digital compact camera": "Camera",
    "dslr/video camera": "Camera",
    "printer/scanner": "Printer / scanner",
}


def _number(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(str(value).strip().replace(",", "."))
        return default if number != number else number
    except (TypeError, ValueError):
        return default


def _truth(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"true", "yes", "1", "y"}


def _canonical(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold()
    text = re.sub(r"\b(incorporated|corporation|company|limited|inc|corp|ltd|llc|gmbh|sa|sas|plc)\b", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _stable_id(*parts: Any, length: int = 16) -> str:
    return hashlib.sha1("|".join(_canonical(part) for part in parts).encode()).hexdigest()[:length]


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/csv;q=0.9, */*;q=0.8"})
    return session


def _fetch_json(url: str, **kwargs: Any) -> Any:
    with _session() as session:
        response = session.get(url, timeout=TIMEOUT, **kwargs)
        response.raise_for_status()
        return response.json()


def _source_record(name: str, url: str, records: int, license_name: str, resource: str = "") -> dict:
    return {
        "name": name,
        "url": url,
        "records": int(records),
        "retrieved_at": RETRIEVED_AT,
        "license": license_name,
        "resource": resource or url,
        "status": "current",
    }


def _computer_category(product_type: str) -> str:
    value = str(product_type).casefold()
    return "Laptop" if any(word in value for word in ("notebook", "tablet", "portable", "mobile workstation")) else "Desktop"


def _estimated_fields(category: str) -> dict[str, Any]:
    base = CATEGORY_BASELINES.get(category, CATEGORY_BASELINES["Other"])
    return {
        "manufacturing_kg": base["manufacturing"],
        "active_power_w": base["power"],
        "daily_hours": 6.0 if category in {"Laptop", "Desktop", "Monitor"} else 24.0 if category == "Router / network" else 5.0,
        "lifespan_years": base["life"],
        "repairability": 5.0,
        "recyclability_pct": 65.0,
        "recycled_content_pct": 20.0,
        "battery_wh": base.get("battery", 0.0),
        "replaceable_battery": False,
        "weight_kg": base["weight"],
        "transport_km": 7000.0,
        "grid_profile": "Average",
        "grid_kg_co2_per_kwh": 0.42,
        "software_support_years": None,
        "observed_battery": False,
        "observed_durability": False,
        "observed_software_support": False,
    }


def _base_record(raw: dict, dataset_id: str, source_name: str, category: str) -> dict:
    model = raw.get("model_name") or raw.get("model_number") or "Unnamed model"
    number = raw.get("model_number") or model
    record = _estimated_fields(category)
    record.update(
        {
            "product_id": f"es-{dataset_id}-{raw.get('pd_id') or _stable_id(model, number)}",
            "name": str(model),
            "model_number": str(number),
            "manufacturer": raw.get("brand_name") or raw.get("energy_star_partner") or "Unknown",
            "category": category,
            "source_name": source_name,
            "source_url": f"https://data.energystar.gov/resource/{dataset_id}.json?pd_id={raw.get('pd_id')}",
            "source_type": "energy_certification",
            "source_license": "U.S. government public data; see source terms",
            "source_retrieved_at": RETRIEVED_AT,
            "market_date": str(raw.get("date_available_on_market", ""))[:10],
            "certification_date": str(raw.get("date_certified") or raw.get("date_qualified") or "")[:10],
            "markets": raw.get("markets", ""),
            "observed_energy": False,
            "observed_repairability": False,
            "observed_carbon": False,
            "observed_field_count": 1,
            "annual_energy_kwh": None,
            "screen_size_inches": None,
            "raw_product_type": raw.get("type") or raw.get("display_type") or raw.get("product_type") or "",
        }
    )
    return record


def normalize_energy_star(dataset_id: str, source_name: str, family: str, raw_rows: list[dict]) -> list[dict]:
    records = []
    for raw in raw_rows:
        category = _computer_category(raw.get("type", "")) if family == "Computer" else family
        record = _base_record(raw, dataset_id, source_name, category)
        if family == "Computer":
            annual = _number(raw.get("tec_of_model_kwh"))
            battery = _number(raw.get("total_battery_capacity_watt_hours"))
            if annual is not None:
                record.update(annual_energy_kwh=annual, active_power_w=round(annual / 365 / record["daily_hours"] * 1000, 2), observed_energy=True)
                record["observed_field_count"] += 1
            if battery is not None:
                record.update(battery_wh=battery, observed_battery=True)
                record["observed_field_count"] += 1
        elif family == "Monitor":
            power = _number(raw.get("on_mode_power_watts"))
            size = _number(raw.get("screen_size_inches"))
            if power is not None:
                record.update(active_power_w=power, observed_energy=True)
                record["observed_field_count"] += 1
            record["screen_size_inches"] = size
        elif family == "Television":
            power = _number(raw.get("power_consumption_in_on_mode_watts"))
            annual = _number(raw.get("reported_annual_energy_consumption_kwh"))
            size = _number(raw.get("diagonal_viewable_screen_size_inches"))
            if power is not None or annual is not None:
                record.update(active_power_w=power or record["active_power_w"], annual_energy_kwh=annual, observed_energy=True)
                record["observed_field_count"] += 1
            record["screen_size_inches"] = size
        elif family == "Router / network":
            load_fields = [
                "typ_power_config_full_port_full_load_power_measured_watts",
                "typ_power_config_full_port_30_percent_load_power_measured_watts",
                "typ_power_config_full_port_10_percent_load_power_measured_watts",
            ]
            loads = [_number(raw.get(field)) for field in load_fields]
            loads = [value for value in loads if value is not None]
            if loads:
                power = sum(loads) / len(loads)
                record.update(active_power_w=round(power, 2), annual_energy_kwh=round(power * 24 * 365 / 1000, 2), observed_energy=True)
                record["observed_field_count"] += 1
        else:
            sleep = _number(raw.get("power_in_sleep_w"))
            standby = _number(raw.get("power_in_standby_w"))
            if sleep is not None:
                record.update(active_power_w=max(sleep * 6, 5), observed_energy=True)
                record["observed_field_count"] += 1
            record["standby_power_w"] = standby
        records.append(record)
    return records


def fetch_energy_star() -> tuple[list[dict], list[dict]]:
    records, sources = [], []
    for dataset_id, (name, family) in ENERGY_STAR.items():
        resource = f"https://data.energystar.gov/resource/{dataset_id}.json?$limit=50000"
        raw = _fetch_json(resource)
        records.extend(normalize_energy_star(dataset_id, name, family, raw))
        sources.append(_source_record(name, f"https://data.energystar.gov/d/{dataset_id}", len(raw), "U.S. government public data", resource))
    return records, sources


def fetch_eprel() -> tuple[list[dict], dict]:
    headers = {"Referer": EPREL_PAGE, "Accept": "application/json", "User-Agent": USER_AGENT}
    api_key = os.getenv("EPREL_API_KEY")
    if api_key:
        headers["X-API-Key"] = api_key
    hits, page, total = [], 1, None
    with _session() as session:
        while total is None or len(hits) < total:
            response = session.get(
                EPREL_ENDPOINT,
                params={"_page": page, "_limit": 100, "sort0": "onMarketStartDateTS", "order0": "DESC"},
                headers=headers,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            batch = payload.get("hits", [])
            total = int(payload.get("size", len(batch)))
            if not batch:
                break
            hits.extend(batch)
            page += 1
            if page > 100:
                raise RuntimeError("EPREL pagination exceeded safety limit")

    records = []
    for raw in hits:
        category = "Tablet" if str(raw.get("deviceType", "")).upper() == "TABLET" else "Smartphone"
        registration = raw.get("eprelRegistrationNumber") or raw.get("registrationNumber") or _stable_id(raw.get("supplierOrTrademark"), raw.get("modelIdentifier"))
        repair_native = _number(raw.get("repairabilityIndex"))
        battery_mah = _number(raw.get("ratedBatteryCapacity"))
        battery_cycles_raw = _number(raw.get("batteryEnduranceInCycles"))
        # The public search endpoint encodes the UI's 500-cycle value as 5.
        battery_cycles = battery_cycles_raw * 100 if battery_cycles_raw is not None and battery_cycles_raw <= 10 else battery_cycles_raw
        software = _number(raw.get("minYearsSoftwareUpdates"))
        battery_present = battery_mah is not None
        record = _estimated_fields(category)
        record.update(
            {
                "product_id": f"eprel-{registration}",
                "name": str(raw.get("modelIdentifier") or "Unnamed model"),
                "model_number": str(raw.get("modelIdentifier") or registration),
                "manufacturer": str(raw.get("supplierOrTrademark") or "Unknown"),
                "category": category,
                "source_name": "EU EPREL Smartphones & Tablets",
                "source_url": f"https://eprel.ec.europa.eu/screen/product/smartphonestablets20231669/{registration}",
                "source_type": "regulatory_registry",
                "source_license": "EU public registry; reuse subject to EPREL terms",
                "source_retrieved_at": RETRIEVED_AT,
                "market_date": str(raw.get("onMarketStartDate") or "")[:10],
                "certification_date": "",
                "markets": "European Union",
                "observed_energy": False,
                "observed_repairability": repair_native is not None,
                "observed_carbon": False,
                "observed_battery": battery_present,
                "observed_durability": raw.get("repeatedFreeFallReliabilityClass") is not None,
                "observed_software_support": software is not None,
                "observed_field_count": 1 + sum((repair_native is not None, battery_present, raw.get("repeatedFreeFallReliabilityClass") is not None, software is not None)),
                "annual_energy_kwh": None,
                "screen_size_inches": None,
                "raw_product_type": raw.get("deviceType"),
                "repairability": round(repair_native * 2, 2) if repair_native is not None else 5.0,
                "repairability_index_native": repair_native,
                "repairability_scale_native": "0–5",
                "repairability_class": raw.get("repairabilityClass"),
                "energy_class": raw.get("energyClass"),
                "durability_class": raw.get("repeatedFreeFallReliabilityClass"),
                "falls_without_defect": _number(raw.get("fallsWithoutDefect")),
                "ingress_protection_rating": raw.get("ingressProtectionRating"),
                "battery_capacity_mah": battery_mah,
                "battery_wh": round(battery_mah * 3.85 / 1000, 2) if battery_mah is not None else record["battery_wh"],
                "battery_voltage_assumption": "3.85 V nominal used to convert published mAh to estimated Wh" if battery_mah is not None else "",
                "battery_endurance_hours": _number(raw.get("batteryEndurancePerCycleInHours")),
                "battery_cycles": battery_cycles,
                "battery_cycles_raw": battery_cycles_raw,
                "replaceable_battery": _truth(raw.get("batteryUserReplaceable")),
                "software_support_years": software,
            }
        )
        records.append(record)
    return records, _source_record("EU EPREL Smartphones & Tablets", EPREL_PAGE, len(records), "EU public registry; see EPREL terms", EPREL_ENDPOINT)


def fetch_repairability() -> tuple[list[dict], dict]:
    metadata = _fetch_json(REPAIR_DATASET_API)
    csv_resources = [item for item in metadata.get("resources", []) if item.get("format", "").lower() == "csv" and "dernière version" in item.get("title", "").casefold()]
    if not csv_resources:
        csv_resources = [item for item in metadata.get("resources", []) if item.get("format", "").lower() == "csv"]
    if not csv_resources:
        raise RuntimeError("Official repairability dataset has no CSV resource")
    resource = csv_resources[0]
    frame = pd.read_csv(resource["url"], low_memory=False)
    mapping = {"smartphone": "Smartphone", "ordinateur portable": "Laptop", "tablette": "Tablet"}
    normalized = frame["categorie_produit"].astype(str).str.casefold()
    frame = frame[normalized.isin(mapping)].copy()
    frame["_category"] = normalized.loc[frame.index].map(mapping)
    records = []
    for _, raw in frame.iterrows():
        category = raw["_category"]
        record = _estimated_fields(category)
        repair = _number(raw.get("note_ir"), 5.0)
        record.update(
            {
                "product_id": f"fr-{raw.get('id_unique') or _stable_id(raw.get('nom_metteur_sur_le_marche'), raw.get('id_modele'))}",
                "name": str(raw.get("nom_modele") or raw.get("id_modele")),
                "model_number": str(raw.get("id_modele") or raw.get("nom_modele")),
                "manufacturer": str(raw.get("nom_metteur_sur_le_marche") or "Unknown"),
                "category": category,
                "source_name": "French Repairability Index",
                "source_url": raw.get("url_tableau_detail_notation") if pd.notna(raw.get("url_tableau_detail_notation")) else REPAIR_PAGE,
                "source_type": "regulatory_repair_index",
                "source_license": "Licence Ouverte / Open Licence 2.0",
                "source_retrieved_at": RETRIEVED_AT,
                "market_date": str(raw.get("date_calcul") or "")[:10],
                "certification_date": "",
                "markets": "France",
                "observed_energy": False,
                "observed_repairability": True,
                "observed_carbon": False,
                "repairability": repair,
                "observed_field_count": 2,
                "annual_energy_kwh": None,
                "screen_size_inches": None,
                "raw_product_type": raw.get("categorie_produit"),
            }
        )
        records.append(record)
    source = _source_record("French Repairability Index", REPAIR_PAGE, len(records), "Licence Ouverte / Open Licence 2.0", resource["url"])
    return records, source


def fetch_ifixit() -> tuple[list[dict], dict]:
    with _session() as session:
        response = session.get(IFIXIT_CSV, timeout=TIMEOUT)
        response.raise_for_status()
    frame = pd.read_csv(StringIO(response.text))
    records = []
    for position, raw in frame.iterrows():
        record = _estimated_fields("Smartphone")
        manufacturer = str(raw.get("OEM") or "Unknown")
        device = str(raw.get("Device") or "Unnamed model")
        release_order = raw.get("Release Order")
        record.update(
            {
                "product_id": f"ifixit-{int(release_order) if pd.notna(release_order) else position}",
                "name": device,
                "model_number": device,
                "manufacturer": manufacturer,
                "category": "Smartphone",
                "source_name": "iFixit Smartphone Repairability",
                "source_url": IFIXIT_PAGE,
                "source_type": "independent_teardown",
                "source_license": "See iFixit source terms",
                "source_retrieved_at": RETRIEVED_AT,
                "market_date": str(int(raw["Date"])) if pd.notna(raw.get("Date")) else "",
                "certification_date": "",
                "markets": "Global / independent teardown",
                "observed_energy": False,
                "observed_repairability": True,
                "observed_carbon": False,
                "repairability": _number(raw.get("Score"), 5.0),
                "observed_field_count": 2,
                "annual_energy_kwh": None,
                "screen_size_inches": None,
                "raw_product_type": "Smartphone",
            }
        )
        records.append(record)
    return records, _source_record("iFixit Smartphone Repairability", IFIXIT_PAGE, len(records), "See iFixit source terms", IFIXIT_CSV)


def manufacturer_report_records() -> tuple[list[dict], list[dict]]:
    records = []
    for item in MANUFACTURER_REPORTS:
        record = _estimated_fields(item["category"])
        share = item.get("manufacturing_share")
        if share is not None:
            record["manufacturing_kg"] = round(item["total"] * share, 2)
        record.update(
            {
                "product_id": f"report-{_stable_id(item['manufacturer'], item['name'], item['year'])}",
                "name": item["name"],
                "model_number": item["name"],
                "manufacturer": item["manufacturer"],
                "category": item["category"],
                "source_name": f"{item['manufacturer']} Product Environmental Report",
                "source_url": item["url"],
                "source_type": "manufacturer_report",
                "source_license": "Copyright manufacturer; factual footprint transcribed with source",
                "source_retrieved_at": RETRIEVED_AT,
                "market_date": str(item["year"]),
                "certification_date": "",
                "markets": "Report configuration; see source",
                "observed_energy": False,
                "observed_repairability": False,
                "observed_carbon": True,
                "reported_lifecycle_kg": item["total"],
                "observed_field_count": 2,
                "annual_energy_kwh": None,
                "screen_size_inches": None,
                "raw_product_type": item["category"],
                "report_boundary": "Cradle-to-grave product footprint; configuration and geography vary by report",
            }
        )
        records.append(record)
    sources = []
    for manufacturer in sorted({item["manufacturer"] for item in MANUFACTURER_REPORTS}):
        subset = [item for item in MANUFACTURER_REPORTS if item["manufacturer"] == manufacturer]
        sources.append(_source_record(f"{manufacturer} Product Environmental Reports", subset[0]["url"], len(subset), "Copyright manufacturer; factual values linked to source"))
    return records, sources


def fetch_open_repair_profiles() -> tuple[pd.DataFrame, dict]:
    with _session() as session:
        response = session.get(OPEN_REPAIR_CSV, timeout=TIMEOUT)
        response.raise_for_status()
    usecols = ["product_category", "brand", "product_age", "repair_status", "repair_barrier_if_end_of_life"]
    frame = pd.read_csv(BytesIO(response.content), usecols=usecols, low_memory=False)
    frame["category"] = frame["product_category"].astype(str).str.strip().str.casefold().map(OPEN_REPAIR_CATEGORY_MAP)
    frame = frame[frame["category"].notna()].copy()
    frame["manufacturer"] = frame["brand"].fillna("Unknown").astype(str).str.strip().replace("", "Unknown")
    frame["canonical_manufacturer"] = frame["manufacturer"].map(_canonical)
    status = frame["repair_status"].fillna("").astype(str).str.casefold()
    frame["fixed"] = status.str.contains(r"\bfixed\b|successful", regex=True)
    frame["repairable"] = frame["fixed"] | status.str.contains("repairable", regex=False)
    frame["end_of_life"] = status.str.contains("end of life", regex=False)
    frame["product_age"] = pd.to_numeric(frame["product_age"], errors="coerce")

    grouped = frame.groupby(["category", "canonical_manufacturer"], dropna=False)
    profiles = grouped.agg(
        manufacturer=("manufacturer", lambda values: values.mode().iat[0] if not values.mode().empty else "Unknown"),
        repair_attempts=("repair_status", "size"),
        fixed_rate=("fixed", "mean"),
        repairable_rate=("repairable", "mean"),
        end_of_life_rate=("end_of_life", "mean"),
        median_product_age=("product_age", "median"),
    ).reset_index()
    barriers = (
        frame.dropna(subset=["repair_barrier_if_end_of_life"])
        .groupby(["category", "canonical_manufacturer"])["repair_barrier_if_end_of_life"]
        .agg(lambda values: values.astype(str).mode().iat[0] if not values.astype(str).mode().empty else "")
        .rename("common_end_of_life_barrier")
        .reset_index()
    )
    profiles = profiles.merge(barriers, on=["category", "canonical_manufacturer"], how="left")
    profiles = profiles[profiles["repair_attempts"] >= 5].sort_values(["category", "repair_attempts"], ascending=[True, False])
    profiles["profile_scope"] = "Aggregated brand/category repair events; not model-specific"
    profiles["source_url"] = OPEN_REPAIR_PAGE
    profiles["source_retrieved_at"] = RETRIEVED_AT
    profiles.to_csv(REPAIR_PROFILES, index=False)
    source = _source_record("Open Repair Data", OPEN_REPAIR_PAGE, len(frame), "CC BY-SA 4.0", OPEN_REPAIR_CSV)
    source["profiles"] = len(profiles)
    return profiles, source


def fetch_grid_intensity() -> tuple[pd.DataFrame, dict]:
    with _session() as session:
        response = session.get(GRID_CSV, timeout=TIMEOUT)
        response.raise_for_status()
    frame = pd.read_csv(StringIO(response.text))
    value_columns = [column for column in frame.columns if "carbon intensity" in column.casefold()]
    if not value_columns:
        raise RuntimeError("Carbon-intensity column not found in electricity dataset")
    value_column = value_columns[0]
    frame[value_column] = pd.to_numeric(frame[value_column], errors="coerce")
    latest = frame.dropna(subset=[value_column]).sort_values("Year").groupby("Entity", as_index=False).tail(1)
    grid = latest[["Entity", "Code", "Year", value_column]].rename(
        columns={"Entity": "region", "Code": "code", "Year": "year", value_column: "g_co2e_per_kwh"}
    )
    grid["kg_co2e_per_kwh"] = grid["g_co2e_per_kwh"] / 1000
    grid["source_name"] = "Our World in Data / Ember"
    grid["source_url"] = GRID_PAGE
    grid["source_retrieved_at"] = RETRIEVED_AT
    grid.sort_values("region").to_csv(GRID_OUTPUT, index=False)
    source = _source_record("Electricity carbon intensity (Our World in Data / Ember)", GRID_PAGE, len(grid), "CC BY 4.0; see source notes", GRID_CSV)
    source["latest_year"] = int(grid["year"].max())
    return grid, source


def _freshness_status(row: pd.Series) -> str:
    text = str(row.get("market_date") or row.get("certification_date") or "")
    match = re.search(r"(20\d{2})", text)
    if not match:
        return "unknown"
    age = date.today().year - int(match.group(1))
    return "recent" if age <= 2 else "active" if age <= 5 else "legacy"


def resolve_entities(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["manufacturer"] = frame["manufacturer"].fillna("Unknown").astype(str).str.strip()
    frame["name"] = frame["name"].fillna("").astype(str).str.strip()
    frame["model_number"] = frame["model_number"].fillna(frame["name"]).astype(str).str.strip()
    frame["canonical_manufacturer"] = frame["manufacturer"].map(_canonical)
    model_identity = frame["model_number"].where(~frame["model_number"].str.casefold().isin({"", "nan", "unnamed model"}), frame["name"])
    frame["canonical_model"] = model_identity.map(_canonical)
    missing_model = frame["canonical_model"].eq("")
    frame.loc[missing_model, "canonical_model"] = frame.loc[missing_model, "product_id"].map(_canonical)
    frame["entity_key"] = [
        _stable_id(category, manufacturer, model)
        for category, manufacturer, model in zip(frame["category"], frame["canonical_manufacturer"], frame["canonical_model"])
    ]
    frame["_rank"] = pd.to_numeric(frame["observed_field_count"], errors="coerce").fillna(0)
    frame["_rank"] += frame["source_type"].map({"manufacturer_report": 4, "regulatory_registry": 3, "regulatory_repair_index": 2, "energy_certification": 2, "independent_teardown": 1}).fillna(0)
    frame = frame.sort_values(["entity_key", "source_name", "_rank", "market_date"], ascending=[True, True, False, False])
    frame = frame.drop_duplicates(["entity_key", "source_name"], keep="first")
    frame["duplicate_group_size"] = frame.groupby("entity_key")["entity_key"].transform("size")
    frame["is_primary_record"] = False
    primary_index = frame.sort_values(["entity_key", "_rank", "market_date"], ascending=[True, False, False]).groupby("entity_key", sort=False).head(1).index
    frame.loc[primary_index, "is_primary_record"] = True
    frame["evidence_source_count"] = frame.groupby("entity_key")["source_name"].transform("nunique")
    frame["freshness_status"] = frame.apply(_freshness_status, axis=1)
    observed = pd.to_numeric(frame["observed_field_count"], errors="coerce").fillna(0)
    frame["data_quality"] = pd.cut(observed, bins=[-1, 1, 3, 99], labels=["Limited", "Moderate", "Strong"]).astype(str)
    return frame.drop(columns=["_rank"])


def sync() -> pd.DataFrame:
    DATA_DIR.mkdir(exist_ok=True)
    records, sources = fetch_energy_star()
    eprel_records, eprel_source = fetch_eprel()
    records.extend(eprel_records)
    sources.append(eprel_source)
    repair_records, repair_source = fetch_repairability()
    records.extend(repair_records)
    sources.append(repair_source)
    ifixit_records, ifixit_source = fetch_ifixit()
    records.extend(ifixit_records)
    sources.append(ifixit_source)
    report_records, report_sources = manufacturer_report_records()
    records.extend(report_records)
    sources.extend(report_sources)

    repair_profiles, open_repair_source = fetch_open_repair_profiles()
    sources.append(open_repair_source)
    _, grid_source = fetch_grid_intensity()
    sources.append(grid_source)

    frame = pd.DataFrame(records)
    frame = frame[frame["name"].astype(str).str.strip().ne("") & frame["manufacturer"].astype(str).str.strip().ne("")]
    frame = frame.drop_duplicates("product_id")
    frame = resolve_entities(frame)
    profile_columns = ["category", "canonical_manufacturer", "repair_attempts", "fixed_rate", "repairable_rate", "end_of_life_rate", "median_product_age", "common_end_of_life_barrier"]
    frame = frame.merge(repair_profiles[profile_columns], on=["category", "canonical_manufacturer"], how="left")
    frame = frame.rename(columns={"fixed_rate": "brand_repair_success_rate"})
    frame = frame.sort_values(["category", "manufacturer", "name", "source_name"]).reset_index(drop=True)
    frame.to_csv(OUTPUT, index=False)

    digest_columns = ["product_id", "entity_key", "source_name", "market_date", "observed_field_count"]
    snapshot_id = hashlib.sha256(frame[digest_columns].to_csv(index=False).encode()).hexdigest()[:16]
    summary = {
        "schema_version": "3.0",
        "snapshot_id": snapshot_id,
        "generated_at": RETRIEVED_AT,
        "snapshot_date": date.today().isoformat(),
        "record_count": len(frame),
        "unique_entities": int(frame["entity_key"].nunique()),
        "manufacturer_count": int(frame["manufacturer"].nunique()),
        "categories": frame["category"].value_counts().to_dict(),
        "freshness": frame["freshness_status"].value_counts().to_dict(),
        "data_quality": frame["data_quality"].value_counts().to_dict(),
        "duplicate_evidence_records": int((frame["duplicate_group_size"] > 1).sum()),
        "repair_profiles": len(repair_profiles),
        "grid_regions": grid_source["records"],
        "sources": sources,
        "license_note": "Source-specific licences and terms apply. Every record retains its source URL and retrieval time.",
        "method_note": "Observed source fields are kept separate from category assumptions. Entity resolution groups complementary evidence without inventing merged measurements.",
    }
    METADATA.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return frame


if __name__ == "__main__":
    sync()
