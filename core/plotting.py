from __future__ import annotations

import pandas as pd
import plotly.express as px


def histogram_plot(converted_long_df: pd.DataFrame, section: str, histnorm: str = ""):
    df = converted_long_df[
        (converted_long_df["section"] == section) & converted_long_df["converted_score"].notna()
    ].copy()

    if df.empty:
        return None

    fig = px.histogram(
        df,
        x="converted_score",
        color="table_name",
        barmode="overlay",
        opacity=0.6,
        histnorm=histnorm,
        nbins=40,
        title=f"Converted Score Distribution — {section}",
    )
    fig.update_layout(legend_title_text="Table")
    return fig


def ecdf_plot(converted_long_df: pd.DataFrame, section: str):
    df = converted_long_df[
        (converted_long_df["section"] == section) & converted_long_df["converted_score"].notna()
    ].copy()

    if df.empty:
        return None

    fig = px.ecdf(
        df,
        x="converted_score",
        color="table_name",
        title=f"ECDF of Converted Scores — {section}",
    )
    fig.update_layout(legend_title_text="Table")
    return fig


def box_plot(converted_long_df: pd.DataFrame, section: str):
    df = converted_long_df[
        (converted_long_df["section"] == section) & converted_long_df["converted_score"].notna()
    ].copy()

    if df.empty:
        return None

    fig = px.box(
        df,
        x="table_name",
        y="converted_score",
        points=False,
        title=f"Box Plot of Converted Score — {section}",
    )
    fig.update_layout(xaxis_title="Table", yaxis_title="Converted Score")
    return fig


def delta_histogram_plot(wide_df: pd.DataFrame, delta_col: str):
    if wide_df.empty or delta_col not in wide_df.columns:
        return None

    df = wide_df[[delta_col]].dropna().copy()
    if df.empty:
        return None

    fig = px.histogram(df, x=delta_col, nbins=40, title=f"Distribution of {delta_col}")
    return fig