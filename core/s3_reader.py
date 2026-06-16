from io import BytesIO
import pandas as pd
import streamlit as st


def list_s3_files_with_session(bucket: str, prefix: str) -> list[str]:
    session = st.session_state.get("aws_boto3_session")

    if session is None:
        raise RuntimeError("No AWS session found. Please assume role first.")

    s3 = session.client("s3")

    response = s3.list_objects_v2(
        Bucket=bucket,
        Prefix=prefix,
    )

    return [
        obj["Key"]
        for obj in response.get("Contents", [])
        if not obj["Key"].endswith("/")
    ]


def read_s3_file_with_session(bucket: str, key: str) -> pd.DataFrame:
    session = st.session_state.get("aws_boto3_session")

    if session is None:
        raise RuntimeError("No AWS session found. Please assume role first.")

    s3 = session.client("s3")
    obj = s3.get_object(Bucket=bucket, Key=key)
    data = obj["Body"].read()

    key_lower = key.lower()

    if key_lower.endswith(".parquet"):
        return pd.read_parquet(BytesIO(data))

    if key_lower.endswith(".tsv") or key_lower.endswith(".txt"):
        return pd.read_csv(BytesIO(data), sep="\t")

    return pd.read_csv(BytesIO(data))