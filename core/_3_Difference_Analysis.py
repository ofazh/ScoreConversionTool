import streamlit as st

from core.conversion import add_delta_columns
from core.plotting import delta_histogram_plot
from core.summaries import delta_summary, movement_table

st.title("Difference Analysis")

wide_df = st.session_state.get("comparison_wide_df")
converted_long_df = st.session_state.get("converted_long_df")

if wide_df is None or converted_long_df is None or wide_df.empty or converted_long_df.empty:
    st.warning("No comparison data yet. Please run Compare Distributions first.")
    st.info("Use the sidebar to go back to Upload/Validate or Compare Distributions.")
else:
    st.success("Wide comparison data loaded.")

    available_sections = sorted(converted_long_df["section"].dropna().unique().tolist())

    if not available_sections:
        st.warning("No sections found in converted data.")
    else:
        section = st.radio("Section", options=available_sections, horizontal=True)

        section_ids = (
            converted_long_df.loc[
                converted_long_df["section"] == section, "student_id"
            ]
            .dropna()
            .unique()
            .tolist()
        )

        section_wide_df = wide_df[wide_df["student_id"].isin(section_ids)].copy()

        score_columns = [
            c for c in section_wide_df.columns
            if c not in [
                "student_id",
                "theta",
                "section",
                "event_id",
                "num_correct",
                "form",
                "administration",
                "group",
                "subgroup",
            ]
            and not c.startswith("delta_")
        ]

        score_columns = [
            c for c in score_columns
            if section_wide_df[c].dtype.kind in "biufc"
        ]

        if len(score_columns) < 2:
            st.warning("Need at least two conversion tables for difference analysis.")
            st.write("Detected score columns:", score_columns)
        else:
            reference_table = st.selectbox(
                "Reference table",
                options=score_columns,
                index=0,
            )

            comparison_tables = st.multiselect(
                "Comparison table(s)",
                options=[c for c in score_columns if c != reference_table],
                default=[c for c in score_columns if c != reference_table][:1],
            )

            if not comparison_tables:
                st.warning("Select at least one comparison table.")
            else:
                section_wide_df = add_delta_columns(
                    section_wide_df,
                    reference_table,
                    comparison_tables,
                )

                delta_cols = [
                    c for c in section_wide_df.columns
                    if c.startswith("delta_")
                ]

                if not delta_cols:
                    st.info("No delta columns could be generated.")
                else:
                    st.subheader("Delta summary")
                    st.dataframe(delta_summary(section_wide_df), use_container_width=True)

                    selected_delta = st.selectbox(
                        "Choose a delta variable",
                        options=delta_cols,
                    )

                    fig = delta_histogram_plot(section_wide_df, selected_delta)
                    if fig is not None:
                        st.plotly_chart(fig, use_container_width=True)

                    st.subheader("Score movement table")
                    st.dataframe(
                        movement_table(section_wide_df, selected_delta),
                        use_container_width=True,
                    )

                    st.subheader("Student-level delta data")
                    display_cols = [
                        c
                        for c in ["student_id", reference_table]
                        + comparison_tables
                        + delta_cols
                        if c in section_wide_df.columns
                    ]
                    st.dataframe(
                        section_wide_df[display_cols].head(200),
                        use_container_width=True,
                    )