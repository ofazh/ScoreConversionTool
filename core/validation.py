from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import pandas as pd

from core.schemas import ConversionSchema, VALID_SECTIONS


@dataclass
class ValidationResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    infos: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


def validate_student_file(df: pd.DataFrame) -> ValidationResult:
    result = ValidationResult()

    required_cols = {"student_id", "section", "theta"}
    missing = required_cols - set(df.columns)

    if missing:
        result.errors.append(
            f"Student file is missing required mapped columns: {sorted(missing)}"
        )
        return result

    if df["student_id"].isna().all():
        result.errors.append("All values in student_id are missing.")
    elif df["student_id"].isna().any():
        result.warnings.append("Some rows have missing student_id values.")

    if df["section"].isna().all():
        result.errors.append("All values in section are missing.")
    elif df["section"].isna().any():
        result.warnings.append("Some rows have missing section values.")

    if not pd.api.types.is_numeric_dtype(df["theta"]):
        result.errors.append("Mapped theta column must be numeric.")
    else:
        n_missing = int(df["theta"].isna().sum())
        if n_missing > 0:
            result.warnings.append(f"Theta has {n_missing} missing values.")

    detected_sections = sorted(df["section"].dropna().astype(str).unique().tolist())
    if detected_sections:
        invalid_sections = sorted(set(detected_sections) - set(VALID_SECTIONS))
        if invalid_sections:
            result.warnings.append(
                f"Unrecognized section values found: {invalid_sections}. Expected values include {VALID_SECTIONS}."
            )

    result.infos.append(f"Student file has {len(df):,} rows.")
    if detected_sections:
        result.infos.append(f"Detected sections: {detected_sections}")

    return result


def validate_conversion_table(df: pd.DataFrame, source_name: str = "") -> ValidationResult:
    result = ValidationResult()
    required = set(ConversionSchema().required_columns)

    missing = required - set(df.columns)
    if missing:
        result.errors.append(
            f"Conversion table '{source_name}' is missing required columns: {sorted(missing)}"
        )
        return result

    for col in ["theta_min", "theta_max", "scale_score"]:
        if not pd.api.types.is_numeric_dtype(df[col]):
            result.errors.append(f"Column '{col}' must be numeric in '{source_name}'.")

    if result.errors:
        return result

    invalid_sections = sorted(set(df["section"].dropna()) - set(VALID_SECTIONS))
    if invalid_sections:
        result.warnings.append(
            f"Conversion table '{source_name}' contains nonstandard sections: {invalid_sections}."
        )

    if (df["theta_max"] <= df["theta_min"]).any():
        bad_n = int((df["theta_max"] <= df["theta_min"]).sum())
        result.errors.append(
            f"Conversion table '{source_name}' has {bad_n} rows where theta_max <= theta_min."
        )

    duplicate_n = int(df.duplicated().sum())
    if duplicate_n > 0:
        result.warnings.append(
            f"Conversion table '{source_name}' has {duplicate_n} duplicated rows."
        )

    for (table_name, section), grp in df.groupby(["table_name", "section"], dropna=False):
        grp_sorted = grp.sort_values(["theta_min", "theta_max"]).reset_index(drop=True)
        overlaps = 0
        gaps = 0

        for i in range(1, len(grp_sorted)):
            prev_max = grp_sorted.loc[i - 1, "theta_max"]
            curr_min = grp_sorted.loc[i, "theta_min"]
            if curr_min < prev_max:
                overlaps += 1
            elif curr_min > prev_max:
                gaps += 1

        if overlaps > 0:
            result.errors.append(
                f"Table '{table_name}', section '{section}' has {overlaps} overlapping interval pair(s)."
            )
        if gaps > 0:
            result.warnings.append(
                f"Table '{table_name}', section '{section}' has {gaps} gap(s) between intervals."
            )
        if grp_sorted["scale_score"].diff().dropna().lt(0).any():
            result.warnings.append(
                f"Table '{table_name}', section '{section}' has non-monotonic scale scores."
            )

    result.infos.append(
        f"Conversion table '{source_name}' has {len(df):,} rows and passed structural checks."
    )
    return result