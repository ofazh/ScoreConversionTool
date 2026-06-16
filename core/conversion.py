from __future__ import annotations

import re
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import streamlit as st

from core.io import read_uploaded_table
from core.prepare import (
    infer_section_from_name,
    infer_test_from_name,
    standardize_conversion_columns,
)
from core.validation import validate_conversion_table


# ============================================================
# Conversion helper functions
# ============================================================
def safe_key(*parts) -> str:
    raw = "_".join(str(p) for p in parts)
    return re.sub(r"[^a-zA-Z0-9_]+", "_", raw)


def read_conversion_workbook(file) -> Dict[str, pd.DataFrame]:
    file_name = file.name.lower()

    if file_name.endswith((".xlsx", ".xls")):
        file.seek(0)
        excel_file = pd.ExcelFile(file)

        sheet_map = {}
        for sheet_name in excel_file.sheet_names:
            sheet_map[sheet_name] = pd.read_excel(
                excel_file,
                sheet_name=sheet_name,
            )

        return sheet_map

    file.seek(0)
    return {"Single table": read_uploaded_table(file)}


def clean_method_name(file_name: str, sheet_name: str) -> str:
    name = f"{file_name}_{sheet_name}"

    for suffix in [".xlsx", ".xls", ".csv", ".sas7bdat", ".xpt", ".rds"]:
        name = name.replace(suffix, "").replace(suffix.upper(), "")

    return name


def enforce_conversion_precision(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    out["theta_min"] = pd.to_numeric(out["theta_min"], errors="coerce").round(6)
    out["theta_max"] = pd.to_numeric(out["theta_max"], errors="coerce").round(6)
    out["scale_score"] = pd.to_numeric(out["scale_score"], errors="coerce")

    return out


# ============================================================
# Core conversion functions used elsewhere in app
# ============================================================
def standardize_students_to_long(student_df: pd.DataFrame) -> pd.DataFrame:
    if {"student_id", "section", "theta"}.issubset(student_df.columns):
        out = student_df.copy()
        out["section"] = out["section"].astype(str)
        return out

    if {"student_id", "theta_rw", "theta_math"}.issubset(student_df.columns):
        id_vars = [c for c in student_df.columns if c not in ["theta_rw", "theta_math"]]
        out = student_df.melt(
            id_vars=id_vars,
            value_vars=["theta_rw", "theta_math"],
            var_name="theta_source",
            value_name="theta",
        )
        out["section"] = out["theta_source"].map(
            {"theta_rw": "RW", "theta_math": "Math"}
        )
        out = out.drop(columns=["theta_source"])
        return out

    raise ValueError("Student file is not in a supported format.")


def assign_scale_scores(
    student_long_df: pd.DataFrame,
    conversion_df: pd.DataFrame,
    include_upper_bound_for_last_interval: bool = True,
) -> pd.DataFrame:
    student_long_df = student_long_df.copy()
    conversion_df = conversion_df.copy()

    conversion_df["theta_min"] = pd.to_numeric(
        conversion_df["theta_min"], errors="coerce"
    ).round(6)
    conversion_df["theta_max"] = pd.to_numeric(
        conversion_df["theta_max"], errors="coerce"
    ).round(6)
    conversion_df["scale_score"] = pd.to_numeric(
        conversion_df["scale_score"], errors="coerce"
    )

    student_long_df["theta"] = pd.to_numeric(
        student_long_df["theta"], errors="coerce"
    )

    outputs: List[pd.DataFrame] = []

    for (table_name, section), table_grp in conversion_df.groupby(
        ["table_name", "section"], dropna=False
    ):
        table_grp = table_grp.sort_values(["theta_min", "theta_max"]).reset_index(
            drop=True
        )

        stu_grp = student_long_df[student_long_df["section"] == section].copy()

        if stu_grp.empty:
            continue

        stu_grp["table_name"] = table_name
        stu_grp["converted_score"] = np.nan
        stu_grp["conversion_match_status"] = "unmatched"

        for i, row in table_grp.iterrows():
            theta_min = row["theta_min"]
            theta_max = row["theta_max"]
            scale_score = row["scale_score"]

            is_last = i == len(table_grp) - 1

            if include_upper_bound_for_last_interval and is_last:
                mask = stu_grp["theta"].ge(theta_min) & stu_grp["theta"].le(theta_max)
            else:
                mask = stu_grp["theta"].ge(theta_min) & stu_grp["theta"].lt(theta_max)

            assignable = mask & stu_grp["converted_score"].isna()

            stu_grp.loc[assignable, "converted_score"] = scale_score
            stu_grp.loc[assignable, "conversion_match_status"] = "matched"

        outputs.append(stu_grp)

    if not outputs:
        return pd.DataFrame()

    out = pd.concat(outputs, ignore_index=True)
    out["converted_score"] = pd.to_numeric(out["converted_score"], errors="coerce")
    return out


def build_wide_comparison(converted_long_df: pd.DataFrame) -> pd.DataFrame:
    if converted_long_df.empty:
        return pd.DataFrame()

    id_cols = [
        c
        for c in converted_long_df.columns
        if c not in ["table_name", "converted_score", "conversion_match_status"]
    ]

    wide = converted_long_df.pivot_table(
        index=id_cols,
        columns="table_name",
        values="converted_score",
        aggfunc="first",
    ).reset_index()

    wide.columns.name = None
    return wide


def add_delta_columns(
    wide_df: pd.DataFrame,
    reference_table: str,
    comparison_tables: List[str],
) -> pd.DataFrame:
    out = wide_df.copy()

    for table in comparison_tables:
        if table == reference_table:
            continue

        if reference_table in out.columns and table in out.columns:
            out[f"delta_{table}_minus_{reference_table}"] = (
                out[table] - out[reference_table]
            )

    return out


# ============================================================
# Streamlit conversion-table UI
# ============================================================
def render_conversion_table_upload_section() -> None:
    st.header("1. Upload Conversion Tables")

    conversion_files = st.file_uploader(
        "Upload one or more conversion tables",
        type=["csv", "xlsx", "xls", "sas7bdat", "xpt", "rds"],
        accept_multiple_files=True,
        key="conversion_uploader",
    )

    if not conversion_files:
        return

    all_tables = []
    validation_map = {}
    table_name_map = {}
    table_section_map = {}
    table_test_map = {}

    st.subheader("Configure conversion tables")

    for file in conversion_files:
        try:
            sheet_map = read_conversion_workbook(file)
        except Exception as exc:
            st.error(f"Failed to read {file.name}: {exc}")
            continue

        available_sheets = list(sheet_map.keys())

        st.markdown(f"### {file.name}")
        st.info(f"Found {len(available_sheets)} sheet/table(s).")

        selected_sheets = st.multiselect(
            "Select workbook tab(s) to import",
            options=available_sheets,
            default=available_sheets,
            key=safe_key("selected_sheets", file.name),
        )

        if not selected_sheets:
            st.warning("No sheets selected for this workbook.")
            continue

        sheet_tabs = st.tabs(selected_sheets)

        for sheet_tab, sheet_name in zip(sheet_tabs, selected_sheets):
            raw_df = sheet_map[sheet_name]

            with sheet_tab:
                st.subheader(f"Sheet: {sheet_name}")
                st.dataframe(raw_df.head(20), use_container_width=True)

                default_method_name = clean_method_name(file.name, sheet_name)

                method_name = st.text_input(
                    "Conversion method name",
                    value=default_method_name,
                    key=safe_key("method_name", file.name, sheet_name),
                ).strip()

                inferred_section = infer_section_from_name(method_name)
                section_options = ["Math", "RW"]

                content_area = st.selectbox(
                    "Content area",
                    section_options,
                    index=(
                        section_options.index(inferred_section)
                        if inferred_section in section_options
                        else 0
                    ),
                    key=safe_key("content_area", file.name, sheet_name),
                )

                inferred_test = infer_test_from_name(method_name)
                test_options = ["SAT", "P10", "P89", "Other"]

                test_name = st.selectbox(
                    "Test / assessment",
                    test_options,
                    index=(
                        test_options.index(inferred_test)
                        if inferred_test in test_options
                        else test_options.index("Other")
                    ),
                    key=safe_key("test_name", file.name, sheet_name),
                )

                use_sheet = st.checkbox(
                    "Use this sheet",
                    value=True,
                    key=safe_key("use_sheet", file.name, sheet_name),
                )

                if not use_sheet:
                    st.info("This sheet will be skipped.")
                    continue

                if not method_name:
                    st.error("Conversion method name cannot be blank.")
                    continue

                try:
                    df = standardize_conversion_columns(
                        raw_df,
                        table_name=method_name,
                        section=content_area,
                    )

                    df = enforce_conversion_precision(df)

                    df["test_name"] = test_name
                    df["source_file"] = file.name
                    df["source_sheet"] = sheet_name

                    source_id = f"{file.name}::{sheet_name}"
                    result = validate_conversion_table(df, source_name=source_id)

                    validation_map[source_id] = result
                    table_name_map[source_id] = method_name
                    table_section_map[source_id] = content_area
                    table_test_map[source_id] = test_name

                    all_tables.append(df)

                    st.success(
                        f"Prepared: {method_name} | {content_area} | {test_name}"
                    )

                    st.dataframe(
                        df.head(20).style.format(
                            {
                                "theta_min": "{:.6f}",
                                "theta_max": "{:.6f}",
                            }
                        ),
                        use_container_width=True,
                    )

                except Exception as exc:
                    source_id = f"{file.name}::{sheet_name}"
                    validation_map[source_id] = exc
                    st.error(f"Failed to process sheet {sheet_name}: {exc}")

    method_names = list(table_name_map.values())
    duplicate_method_problem = len(method_names) != len(set(method_names))

    if duplicate_method_problem:
        st.error("Conversion method names must be unique across all files and sheets.")

    st.session_state["table_name_map"] = table_name_map
    st.session_state["table_section_map"] = table_section_map
    st.session_state["table_test_map"] = table_test_map
    st.session_state["conversion_validations"] = validation_map

    if all_tables and not duplicate_method_problem:
        combined_conversion_df = pd.concat(all_tables, ignore_index=True)
        st.session_state["combined_conversion_df"] = combined_conversion_df

        st.subheader("Combined conversion table preview")
        st.dataframe(
            combined_conversion_df.head(100).style.format(
                {
                    "theta_min": "{:.6f}",
                    "theta_max": "{:.6f}",
                }
            ),
            use_container_width=True,
        )

    st.subheader("Conversion table validation")

    if st.session_state.get("combined_conversion_df") is not None:
        st.success("Previously imported conversion tables are loaded.")
        st.dataframe(
            st.session_state["combined_conversion_df"].head(100).style.format(
                {"theta_min": "{:.6f}", "theta_max": "{:.6f}"}
            ),
            use_container_width=True,
        )

    for source_id, result in validation_map.items():
        method_name = table_name_map.get(source_id, source_id)
        content_area = table_section_map.get(source_id, "Unknown")
        test_name = table_test_map.get(source_id, "Unknown")

        st.markdown(
            f"**{method_name}**  \n"
            f"Source: `{source_id}`  \n"
            f"Content area: **{content_area}**  \n"
            f"Test: **{test_name}**"
        )

        if isinstance(result, Exception):
            st.error(str(result))
            continue

        for msg in result.errors:
            st.error(msg)
        for msg in result.warnings:
            st.warning(msg)
        for msg in result.infos:
            st.info(msg)


# If this file is used directly as a Streamlit page:
render_conversion_table_upload_section()