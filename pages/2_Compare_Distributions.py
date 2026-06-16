import pandas as pd
import plotly.express as px
import streamlit as st

from core.conversion import assign_scale_scores, build_wide_comparison
from core.summaries import frequency_table


CHART_HEIGHT = 700


def style_histogram(fig, y_title):

    fig.update_layout(
        height=CHART_HEIGHT,
        template="plotly_white",
        font=dict(size=16),
        title=dict(
            x=0.5,
            xanchor="center",
            font=dict(size=24),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.03,
            xanchor="center",
            x=0.5,
            title=None,
            font=dict(size=14),
        ),
        margin=dict(l=50, r=40, t=90, b=70),
        bargap=0.02,
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
    )

    fig.update_traces(
        marker_line_width=0.4,
    )

    fig.update_xaxes(
        title="Scale Score",
        showgrid=True,
        gridwidth=1,
        gridcolor="rgba(220,220,220,0.5)",
        zeroline=False,
        tickfont=dict(size=13),
        title_font=dict(size=17),

        # IMPORTANT
        tickmode="linear",
        tick0=200,
        dtick=10,

        showline=True,
        linewidth=1,
        linecolor="black",
        mirror=False,
    )

    fig.update_yaxes(
        title=y_title,
        showgrid=True,
        gridwidth=1,
        gridcolor="rgba(220,220,220,0.5)",
        zeroline=False,
        tickfont=dict(size=13),
        title_font=dict(size=17),
        showline=True,
        linewidth=1,
        linecolor="black",

        # IMPORTANT
        ticksuffix="%" if y_title == "Percent" else "",
        tickformat=".2f",
    )

    return fig


def show_histogram_pair(df, x_col, color_col, title_prefix, nbins=40):

    fig_percent = px.histogram(
        df,
        x=x_col,
        color=color_col,
        barmode="overlay",
        opacity=0.55,
        histnorm="percent",
        nbins=nbins,
        marginal="box",
        title=f"{title_prefix} — Percentage",
    )

    fig_percent = style_histogram(
        fig_percent,
        y_title="Percent",
    )

    st.plotly_chart(fig_percent, use_container_width=True)

    fig_count = px.histogram(
        df,
        x=x_col,
        color=color_col,
        barmode="overlay",
        opacity=0.55,
        histnorm=None,
        nbins=nbins,
        marginal="box",
        title=f"{title_prefix} — Count",
    )

    fig_count = style_histogram(
        fig_count,
        y_title="Count",
    )

    st.plotly_chart(fig_count, use_container_width=True)


st.title("Compare Distributions")

student_df = st.session_state.get("student_df")
conversion_df = st.session_state.get("combined_conversion_df")
student_validation = st.session_state.get("student_validation")
conversion_validations = st.session_state.get("conversion_validations", {})

if student_df is None or student_df.empty:
    st.warning("No standardized student data found. Go to Upload and Validate first.")
    st.stop()

if conversion_df is None or conversion_df.empty:
    st.warning("No conversion tables found. Go to Upload and Validate first.")
    st.stop()

if student_validation is None or not student_validation.is_valid:
    st.warning("Student data failed validation. Please fix it on Upload and Validate.")
    st.stop()

bad_conversion_sources = []
for source_id, result in conversion_validations.items():
    if isinstance(result, Exception):
        bad_conversion_sources.append(source_id)
    elif not result.is_valid:
        bad_conversion_sources.append(source_id)

if bad_conversion_sources:
    st.warning("At least one conversion table failed validation. Please fix it on Upload and Validate.")
    with st.expander("Invalid conversion table sources", expanded=False):
        st.write(bad_conversion_sources)
    st.stop()

student_long_df = student_df.copy()
conversion_df = conversion_df.copy()

for col in ["theta_min", "theta_max"]:
    if col in conversion_df.columns:
        conversion_df[col] = pd.to_numeric(conversion_df[col], errors="coerce").round(6)

if "scale_score" in conversion_df.columns:
    conversion_df["scale_score"] = pd.to_numeric(conversion_df["scale_score"], errors="coerce")

if "theta" in student_long_df.columns:
    student_long_df["theta"] = pd.to_numeric(student_long_df["theta"], errors="coerce")

converted_long_df = assign_scale_scores(student_long_df, conversion_df)
wide_df = build_wide_comparison(converted_long_df)

st.session_state["converted_long_df"] = converted_long_df
st.session_state["comparison_wide_df"] = wide_df

if converted_long_df is None or converted_long_df.empty:
    st.warning("No converted rows were generated. Check section labels and theta intervals.")
    st.stop()

st.success(f"Using uploaded validated data. Converted rows: {len(converted_long_df):,}")

st.header("Comparison setup")

available_sections = sorted(converted_long_df["section"].dropna().unique().tolist())

if not available_sections:
    st.warning("No valid sections are available for comparison.")
    st.stop()

section = st.radio(
    "Section",
    options=available_sections,
    horizontal=True,
    key="compare_section",
)

section_df = converted_long_df[converted_long_df["section"] == section].copy()

available_tests = (
    sorted(section_df["test_name"].dropna().unique().tolist())
    if "test_name" in section_df.columns
    else []
)

if available_tests:
    selected_tests = st.multiselect(
        "Select test / assessment",
        options=available_tests,
        default=available_tests,
        key="compare_tests",
    )
    section_df = section_df[section_df["test_name"].isin(selected_tests)].copy()

available_tables = sorted(section_df["table_name"].dropna().unique().tolist())

if not available_tables:
    st.warning("No conversion methods are available for the current selection.")
    st.stop()

selected_tables = st.multiselect(
    "Select conversion methods to compare",
    options=available_tables,
    default=available_tables,
    key="compare_tables",
)

view_df = section_df[section_df["table_name"].isin(selected_tables)].copy()
view_df.columns = view_df.columns.str.strip()

if view_df.empty:
    st.info("No data are available for the current selection.")
    st.stop()

view_df["converted_score"] = pd.to_numeric(view_df["converted_score"], errors="coerce")

has_production_score = "production_scalescore" in view_df.columns

if has_production_score:
    view_df["production_scalescore"] = pd.to_numeric(
        view_df["production_scalescore"],
        errors="coerce",
    )

    matched_prod_df = view_df.dropna(
        subset=["converted_score", "production_scalescore"]
    ).copy()

    n_prod_equal = int(
        (
            matched_prod_df["converted_score"]
            == matched_prod_df["production_scalescore"]
        ).sum()
    )
else:
    n_prod_equal = None

n_students = (
    view_df["student_id"].nunique()
    if "student_id" in view_df.columns
    else len(view_df)
)
n_rows = len(view_df)
n_tables = view_df["table_name"].nunique()

m1, m2, m3, m4 = st.columns(4)
m1.metric("Students", f"{n_students:,}")
m2.metric("Rows", f"{n_rows:,}")
m3.metric("Methods", f"{n_tables:,}")

if n_prod_equal is not None:
    m4.metric("Prod SS Equal Rows", f"{n_prod_equal:,}")
else:
    m4.metric("Prod SS Equal Rows", "N/A")

tab1, tab2, tab3, tab4 = st.tabs(
    [
        "Prod_SS Comparison",
        "Summary Stats",
        "Frequencies",
        "Student-Level Data",
    ]
)

with tab1:
    st.subheader("Converted Scale Score vs Production Scale Score")

    if not has_production_score:
        st.warning("No production scale score column was mapped on Upload and Validate.")
    else:
        prod_df = view_df.dropna(
            subset=["converted_score", "production_scalescore"]
        ).copy()

        if prod_df.empty:
            st.warning("No rows have both converted score and production scale score.")
        else:
            method_options = sorted(prod_df["table_name"].dropna().unique().tolist())

            selected_method = st.selectbox(
                "Choose conversion method for production comparison",
                options=method_options,
                key="prod_compare_method",
            )

            method_df = prod_df[prod_df["table_name"] == selected_method].copy()

            prod_long = pd.concat(
                [
                    method_df[
                        [
                            "student_id",
                            "section",
                            "table_name",
                            "production_scalescore",
                        ]
                    ]
                    .rename(columns={"production_scalescore": "scale_score"})
                    .assign(score_source="Production"),
                    method_df[
                        [
                            "student_id",
                            "section",
                            "table_name",
                            "converted_score",
                        ]
                    ]
                    .rename(columns={"converted_score": "scale_score"})
                    .assign(score_source="Converted"),
                ],
                ignore_index=True,
            )

            comp_plot_type = st.selectbox(
                "Production comparison plot type",
                ["Histogram", "ECDF", "Box Plot"],
                key="prod_compare_plot_type",
            )

            if comp_plot_type == "Histogram":
                show_histogram_pair(
                    df=prod_long,
                    x_col="scale_score",
                    color_col="score_source",
                    title_prefix=f"{selected_method}: Converted vs Production — {section}",
                    nbins=40,
                )

            elif comp_plot_type == "ECDF":
                fig = px.ecdf(
                    prod_long,
                    x="scale_score",
                    color="score_source",
                    title=f"{selected_method}: Converted vs Production ECDF — {section}",
                )
                fig.update_layout(height=CHART_HEIGHT)
                st.plotly_chart(fig, use_container_width=True)

            else:
                fig = px.box(
                    prod_long,
                    x="score_source",
                    y="scale_score",
                    points=False,
                    title=f"{selected_method}: Converted vs Production Box Plot — {section}",
                )
                fig.update_layout(height=CHART_HEIGHT)
                st.plotly_chart(fig, use_container_width=True)

            st.subheader("Student-level comparison")

            display_cols = [
                c
                for c in [
                    "student_id",
                    "section",
                    "test_name",
                    "table_name",
                    "theta",
                    "production_scalescore",
                    "converted_score",
                ]
                if c in method_df.columns
            ]

            st.dataframe(
                method_df[display_cols],
                use_container_width=True,
            )

with tab2:
    st.subheader("Summary statistics")

    group_cols = [
        c
        for c in ["section", "test_name", "table_name"]
        if c in view_df.columns
    ]

    converted_summary = (
        view_df.groupby(group_cols)["converted_score"]
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
    converted_summary["score_source"] = "Converted"
    converted_summary = converted_summary.rename(columns={"converted_score": "score"})

    summary_frames = [converted_summary]

    if has_production_score:
        prod_df = view_df.dropna(subset=["production_scalescore"]).copy()

        if not prod_df.empty:
            production_summary = (
                prod_df.groupby(group_cols)["production_scalescore"]
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
            production_summary["score_source"] = "Production"
            production_summary = production_summary.rename(
                columns={"production_scalescore": "score"}
            )
            summary_frames.append(production_summary)

    combined_summary = pd.concat(summary_frames, ignore_index=True)

    display_cols = [
        c
        for c in [
            "section",
            "test_name",
            "table_name",
            "score_source",
            "n",
            "mean",
            "std",
            "median",
            "min",
            "max",
        ]
        if c in combined_summary.columns
    ]

    st.dataframe(
        combined_summary[display_cols],
        use_container_width=True,
    )

with tab3:
    st.subheader("Frequency table")
    freq = frequency_table(view_df)
    st.dataframe(freq, use_container_width=True)

with tab4:
    st.subheader("Converted student-level data")
    st.dataframe(view_df, use_container_width=True)