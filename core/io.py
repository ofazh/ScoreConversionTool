from __future__ import annotations

from io import BytesIO
from typing import Dict

import pandas as pd


SUPPORTED_EXTENSIONS = (".csv", ".xlsx", ".xls")


class FileReadError(Exception):
    """Raised when an uploaded file cannot be read."""


def read_uploaded_table(uploaded_file) -> pd.DataFrame:
    """Read a Streamlit uploaded file into a pandas DataFrame."""
    if uploaded_file is None:
        raise FileReadError("No file was provided.")

    name = uploaded_file.name.lower()
    data = uploaded_file.getvalue()

    try:
        if name.endswith(".csv"):
            return pd.read_csv(BytesIO(data))
        if name.endswith(".xlsx") or name.endswith(".xls"):
            return pd.read_excel(BytesIO(data))
    except Exception as exc:
        raise FileReadError(f"Could not read file '{uploaded_file.name}': {exc}") from exc

    raise FileReadError(
        f"Unsupported file type for '{uploaded_file.name}'. Use CSV or Excel."
    )


# def to_excel_bytes(dfs: Dict[str, pd.DataFrame]) -> bytes:
#     """Write multiple DataFrames to an Excel workbook in memory."""
#     output = BytesIO()
#     with pd.ExcelWriter(output, engine="openpyxl") as writer:
#         for sheet_name, df in dfs.items():
#             safe_name = sheet_name[:31] if sheet_name else "Sheet1"
#             df.to_excel(writer, sheet_name=safe_name, index=False)
#     output.seek(0)
#     return output.getvalue()

def to_excel_bytes(dfs: Dict[str, pd.DataFrame]) -> bytes:
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, df in dfs.items():
            safe_name = str(sheet_name)[:31]
            df.to_excel(writer, sheet_name=safe_name, index=False)

    output.seek(0)
    return output.getvalue()


def read_uploaded_table(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()

    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file)

    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file)

    if name.endswith(".sas7bdat"):
        return pd.read_sas(uploaded_file, format="sas7bdat", encoding="latin1")

    if name.endswith(".xpt"):
        return pd.read_sas(uploaded_file, format="xport", encoding="latin1")

    if name.endswith(".rds"):
        try:
            import pyreadr
        except ImportError:
            raise ImportError("Reading .rds files requires pyreadr. Run: pip install pyreadr")

        data = uploaded_file.read()
        result = pyreadr.read_r(io.BytesIO(data))

        if not result:
            raise ValueError("No data frame found in RDS file.")

        return next(iter(result.values()))

    raise ValueError(f"Unsupported file type: {uploaded_file.name}")


def read_excel_all_sheets(uploaded_file) -> dict[str, pd.DataFrame]:
    return pd.read_excel(uploaded_file, sheet_name=None)