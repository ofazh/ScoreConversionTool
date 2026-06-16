from __future__ import annotations

from typing import List, Optional

import pandas as pd


def summarize_converted_scores(
    converted_long_df: pd.DataFrame,
    group_cols: Optional[List[str]] = None,
) -> pd.DataFrame:
    if converted_long_df.empty:
        return pd.DataFrame()

    group_cols = group_cols or ["section", "table_name"]

    summary = (
        converted_long_df.groupby(group_cols)["converted_score"]
        .agg(
            n="count",
            mean="mean",
            std="std",
            median="median",
            min="min",
            max="max",
        )
        .reset_index()
    )
    return summary


def frequency_table(converted_long_df: pd.DataFrame) -> pd.DataFrame:
    if converted_long_df.empty:
        return pd.DataFrame()

    freq = (
        converted_long_df.dropna(subset=["converted_score"])
        .groupby(["section", "table_name", "converted_score"])
        .size()
        .reset_index(name="count")
    )

    freq["percent"] = freq.groupby(["section", "table_name"])["count"].transform(
        lambda s: s / s.sum() * 100
    )
    return freq


def delta_summary(wide_df: pd.DataFrame) -> pd.DataFrame:
    if wide_df.empty:
        return pd.DataFrame()

    delta_cols = [c for c in wide_df.columns if c.startswith("delta_")]
    if not delta_cols:
        return pd.DataFrame()

    rows = []
    for col in delta_cols:
        s = pd.to_numeric(wide_df[col], errors="coerce")
        rows.append(
            {
                "delta_column": col,
                "n": int(s.notna().sum()),
                "mean": s.mean(),
                "std": s.std(),
                "median": s.median(),
                "min": s.min(),
                "max": s.max(),
                "pct_zero": (s.eq(0).mean() * 100) if s.notna().any() else None,
            }
        )
    return pd.DataFrame(rows)


def movement_table(wide_df: pd.DataFrame, delta_col: str) -> pd.DataFrame:
    if wide_df.empty or delta_col not in wide_df.columns:
        return pd.DataFrame()

    s = pd.to_numeric(wide_df[delta_col], errors="coerce")
    movement = (
        s.value_counts(dropna=True)
        .sort_index()
        .rename_axis("score_change")
        .reset_index(name="count")
    )
    movement["percent"] = movement["count"] / movement["count"].sum() * 100
    return movement