import pandas as pd

import re


def infer_section_from_name(name: str) -> str:
    cleaned = str(name).strip().lower()

    # Remove extension-like endings
    cleaned = cleaned.replace(".csv", "").replace(".xlsx", "").replace(".xls", "")

    # Split on common separators
    tokens = re.split(r"[^a-z0-9]+", cleaned)
    tokens = [t for t in tokens if t]

    # Strong RW indicators
    rw_tokens = {"r", "rw", "reading", "reading&writing", "reading_and_writing","reading_writing", "verbal", "ela", "ebrw"}

    # Strong Math indicators
    math_tokens = {"m", "math", "mss", "mathematics"}

    if any(t in rw_tokens for t in tokens):
        return "RW"

    if any(t in math_tokens for t in tokens):
        return "Math"

    # Fallback: startswith rule
    if cleaned.startswith("r"):
        return "RW"

    if cleaned.startswith("m"):
        return "Math"

    return "Unknown"


SECTION_MAP = {
    "rw": "RW",
    "reading and writing": "RW",
    "reading & writing": "RW",
    "reading": "RW",
    "ebrw": "RW",
    "verbal": "RW",
    "math": "Math",
    "mathematics": "Math",
}


def standardize_student_columns(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    rename_map = {
        mapping["student_id"]: "student_id",
        mapping["section"]: "section",
        mapping["theta"]: "theta",
    }

    if mapping.get("event_id"):
        rename_map[mapping["event_id"]] = "event_id"

    if mapping.get("num_correct"):
        rename_map[mapping["num_correct"]] = "num_correct"

    if mapping.get("production_scalescore"):
        rename_map[mapping["production_scalescore"]] = "production_scalescore"

    out = df.rename(columns=rename_map).copy()

    if "section" in out.columns:
        out["section"] = (
            out["section"]
            .astype(str)
            .str.strip()
            .str.lower()
            .map(lambda x: SECTION_MAP.get(x, x))
        )

    # Convert any column whose name starts with theta / THETA to numeric.
    for col in out.columns:
        if str(col).lower().startswith("theta"):
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "production_scalescore" in out.columns:
        out["production_scalescore"] = pd.to_numeric(
            out["production_scalescore"],
            errors="coerce",
        )

    return out

import re
import pandas as pd


def normalize_colname(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


CONVERSION_COLUMN_ALIASES = {
    "theta_min": {
        "thetamin", "theta_min", "theta minimum", "lower", "lowerbound",
        "min", "fromtheta", "thetafrom"
    },
    "theta_max": {
        "thetamax", "theta_max", "theta maximum", "upper", "upperbound",
        "max", "totheta", "thetato"
    },
    "scale_score": {
        "scalescore", "scale_score", "scaledscore", "scaled_score",
        "scaled", "score", "ss"
    },
}


def standardize_conversion_columns(
    df: pd.DataFrame,
    table_name: str | None = None,
    section: str | None = None,
) -> pd.DataFrame:
    out = df.copy()

    normalized_lookup = {
        normalize_colname(c): c
        for c in out.columns
    }

    rename_map = {}

    for canonical, aliases in CONVERSION_COLUMN_ALIASES.items():
        normalized_aliases = {normalize_colname(a) for a in aliases}

        matched = [
            original_col
            for norm_col, original_col in normalized_lookup.items()
            if norm_col in normalized_aliases
        ]

        if matched:
            rename_map[matched[0]] = canonical

    out = out.rename(columns=rename_map)

    required = ["theta_min", "theta_max", "scale_score"]
    missing = [c for c in required if c not in out.columns]

    if missing:
        raise ValueError(
            f"Conversion table is missing required mapped columns: {missing}. "
            f"Available columns: {list(df.columns)}"
        )

    for col in ["theta_min", "theta_max"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").round(6)

    out["scale_score"] = pd.to_numeric(out["scale_score"], errors="coerce")

    if table_name is not None:
        out["table_name"] = table_name

    if section is not None:
        out["section"] = section

    return out


import re


def normalize_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


# ============================================================
# Content area inference
# ============================================================
def infer_section_from_name(name: str) -> str:
    cleaned = normalize_name(name)

    rw_tokens = [
        "rw",
        "reading",
        "verbal",
        "ebrw",
        "english",
    ]

    math_tokens = [
        "math",
        "mss",
        "quant",
        "mathematics",
    ]

    for token in rw_tokens:
        if token in cleaned:
            return "RW"

    for token in math_tokens:
        if token in cleaned:
            return "Math"

    if cleaned.startswith("r"):
        return "RW"

    if cleaned.startswith("m"):
        return "Math"

    return "Unknown"


# ============================================================
# Test/program inference
# ============================================================
def infer_test_from_name(name: str) -> str:
    cleaned = normalize_name(name)

    # ============================================================
    # P10 aliases
    # ============================================================
    p10_tokens = [
        "p10",
        "psat10",
        "pn",
    ]

    for token in p10_tokens:
        if token in cleaned:
            return "P10"

    # ============================================================
    # P89 aliases
    # ============================================================
    p89_tokens = [
        "p89",
        "psat89",
    ]

    for token in p89_tokens:
        if token in cleaned:
            return "P89"

    # ============================================================
    # SAT aliases
    # ============================================================
    sat_tokens = [
        "sat",
    ]

    for token in sat_tokens:
        if token in cleaned:
            return "SAT"

    return "Unknown"