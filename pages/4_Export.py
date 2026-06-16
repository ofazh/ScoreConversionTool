import pandas as pd
import streamlit as st

from core.io import to_excel_bytes
from core.summaries import frequency_table, summarize_converted_scores


st.title("Export Results")

converted_long_df = st.session_state.get("converted_long_df")

if converted_long_df is None or converted_long_df.empty:
    st.warning("No converted data yet. Please run Compare Distributions first.")
    st.stop()

st.success("Export data loaded.")

converted_long_df = converted_long_df.copy()

# Use standardized theta chosen on Upload and Validate page.
if "theta" not in converted_long_df.columns:
    st.warning(
        "No standardized theta column found. "
        "Check Upload and Validate column mapping."
    )

# Matched means converted scale score equals production scale score.
if {"converted_score", "production_scalescore"}.issubset(converted_long_df.columns):

    converted_long_df["converted_score"] = pd.to_numeric(
        converted_long_df["converted_score"],
        errors="coerce",
    )

    converted_long_df["production_scalescore"] = pd.to_numeric(
        converted_long_df["production_scalescore"],
        errors="coerce",
    )

    converted_long_df["matched"] = (
        (
            converted_long_df["converted_score"]
            == converted_long_df["production_scalescore"]
        )
        .fillna(False)
        .astype(int)
    )

else:
    converted_long_df["matched"] = pd.NA

summary_df = summarize_converted_scores(converted_long_df)
freq_df = frequency_table(converted_long_df)

export_book = {
    "converted_long": converted_long_df,
}

if summary_df is not None and not summary_df.empty:
    export_book["summary"] = summary_df

if freq_df is not None and not freq_df.empty:
    export_book["frequency"] = freq_df

st.subheader("Available export datasets")

for name, df in export_book.items():
    st.write(f"**{name}**: {len(df):,} rows × {len(df.columns):,} columns")

st.subheader("Download files")

converted_csv = converted_long_df.to_csv(index=False).encode("utf-8")

st.download_button(
    label="Download converted long CSV",
    data=converted_csv,
    file_name="converted_long.csv",
    mime="text/csv",
    key="download_converted_long_csv",
)

st.divider()

st.subheader("Excel workbook")

st.info(
    "For large files, Excel may take time to prepare. "
    "CSV downloads are recommended for student-level exports."
)

prepare_excel = st.button("Prepare Excel workbook")

if prepare_excel:
    try:
        excel_bytes = to_excel_bytes(export_book)

        st.download_button(
            label="Download Excel workbook",
            data=excel_bytes,
            file_name="score_conversion_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_excel_workbook",
        )

    except Exception as exc:
        st.error(f"Could not create Excel workbook: {exc}")

st.subheader("Preview export dataset")

preview_options = [
    k for k in export_book.keys()
    if k != "summary"
]

choice = st.selectbox(
    "Preview dataset",
    options=preview_options,
)

st.dataframe(
    export_book[choice].head(100),
    use_container_width=True,
)