"""Farm metadata helpers shared by runtime scaffolding and tests.

Kept dependency-free (stdlib only) so it can be unit-tested without a runtime
root, and imported by ``reporting_bootstrap`` when a farm tree is first
scaffolded.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

# FIPS state/territory code -> USPS code. Source: Census TIGER state FIPS.
US_STATE_CODES: dict[str, str] = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
    "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY", "60": "AS", "66": "GU", "69": "MP",
    "72": "PR", "78": "VI",
}


def state_code_from_fips(state_fips: str | None) -> str:
    """Return the USPS state code for a two-digit FIPS value, or ``""``."""
    if state_fips is None:
        return ""
    text = str(state_fips).strip().zfill(2)
    return US_STATE_CODES.get(text, "")


def infer_farm_state(boundary_path: Path, prior_state: str = "") -> str:
    """Resolve a farm's state code.

    Boundary evidence wins: the modal ``state_fips`` on the farm's field
    features describes where the farm actually is, so a legacy or default
    value cannot stick. The prior two-letter value is only a fallback for
    farms without boundaries yet. Returns ``""`` when neither source is
    usable, so callers can write an empty state instead of inventing one
    (the old code wrote ``"IA"`` into every new farm).
    """
    path = Path(boundary_path)
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = {}
        codes: list[str] = []
        for feature in doc.get("features", []):
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties") or {}
            code = state_code_from_fips(props.get("state_fips"))
            if code:
                codes.append(code)
        if codes:
            return Counter(codes).most_common(1)[0][0]

    prior = str(prior_state or "").strip().upper()
    return prior if len(prior) == 2 and prior.isalpha() else ""
