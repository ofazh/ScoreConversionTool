import hashlib
import time
from datetime import date
from io import BytesIO
from pathlib import Path

import boto3
import pandas as pd
import streamlit as st

from core.aws_auth import get_verify_setting


CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

DEFAULT_DATABASE = "pine_msmstscoring_database"
DEFAULT_TABLE = "pine_sas_student_scr_v"

VALID_DATA_EXTENSIONS = (".csv", ".csv.gz", ".txt", ".tsv", ".parquet")


def get_boto3_session():
    if st.session_state.get("aws_boto3_session") is not None:
        return st.session_state["aws_boto3_session"]

    aws = st.secrets["aws"]
    region_name = aws.get("region_name", "us-east-1")
    profile_name = aws.get("profile_name")

    if profile_name:
        return boto3.Session(
            profile_name=profile_name,
            region_name=region_name,
        )

    return boto3.Session(region_name=region_name)


def get_athena_client():
    return get_boto3_session().client(
        "athena",
        verify=get_verify_setting(),
    )


def get_s3_client():
    return get_boto3_session().client(
        "s3",
        verify=get_verify_setting(),
    )


def parse_s3_uri(s3_uri: str):
    uri = s3_uri.replace("s3://", "")
    bucket, key = uri.split("/", 1)
    return bucket, key


def build_student_score_sql(
    event_id: str,
    subject: str,
    parentpanel_prefix: str,
    database: str = DEFAULT_DATABASE,
    table: str = DEFAULT_TABLE,
) -> str:
    event_id = event_id.strip()
    subject = subject.strip().lower()
    parentpanel_prefix = parentpanel_prefix.strip().upper()

    if not event_id:
        raise ValueError("event_id cannot be blank.")

    if subject not in ["math", "reading"]:
        raise ValueError("subject must be either 'math' or 'reading'.")

    if parentpanel_prefix not in ["W", "S", "I", "P", "V"]:
        raise ValueError("parentpanel_prefix must be one of: W, S, I, P, V.")

    return f"""
SELECT
    event_id,
    asmt_id,
    person_id,
    responseid,
    parentpanelid,
    subject,
    test_id,
    num_correct,
    theta_eap,
    theta_eap_routing,
    theta_mle,
    theta_tcc,
    scaled_score,
    std_err,
    csem,
    sci_theta_method,
    sci_theta,
    sci_weighted_composite,
    sci_linear_transformed_score,
    processed_type
FROM "{database}"."{table}"
WHERE event_id IN ('{event_id}');
--   AND subject IN ('{subject}')
--   AND substring(parentpanelid, 1, 1) IN ('{parentpanel_prefix}')
""".strip()


def get_query_hash(sql: str) -> str:
    return hashlib.md5(sql.encode("utf-8")).hexdigest()


def get_cache_path(sql: str) -> Path:
    return CACHE_DIR / f"athena_{get_query_hash(sql)}.parquet"


def coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    numeric_cols = [
        "num_correct",
        "theta_eap",
        "theta_eap_routing",
        "theta_mle",
        "theta_tcc",
        "scaled_score",
        "std_err",
        "csem",
        "sci_theta",
        "sci_weighted_composite",
        "sci_linear_transformed_score",
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def run_athena_query_to_dataframe(sql: str, wait_seconds: float = 1.0) -> pd.DataFrame:
    aws = st.secrets["aws"]
    athena = get_athena_client()

    params = {
        "QueryString": sql,
        "QueryExecutionContext": {
            "Database": aws.get("athena_database", DEFAULT_DATABASE),
        },
        "WorkGroup": aws.get("athena_workgroup", "primary"),
    }

    if aws.get("athena_output_location"):
        params["ResultConfiguration"] = {
            "OutputLocation": aws["athena_output_location"],
        }

    response = athena.start_query_execution(**params)
    query_execution_id = response["QueryExecutionId"]

    while True:
        status_response = athena.get_query_execution(
            QueryExecutionId=query_execution_id
        )
        state = status_response["QueryExecution"]["Status"]["State"]

        if state in ["SUCCEEDED", "FAILED", "CANCELLED"]:
            break

        time.sleep(wait_seconds)

    if state != "SUCCEEDED":
        reason = status_response["QueryExecution"]["Status"].get(
            "StateChangeReason", "Unknown reason"
        )
        raise RuntimeError(f"Athena query failed with state {state}: {reason}")

    result_s3_uri = status_response["QueryExecution"]["ResultConfiguration"].get(
        "OutputLocation"
    )

    if not result_s3_uri:
        raise RuntimeError(
            "Athena query succeeded, but no S3 OutputLocation was returned."
        )

    bucket, key = parse_s3_uri(result_s3_uri)
    df = read_s3_data_file(bucket=bucket, key=key)
    return coerce_numeric_columns(df)


def load_athena_with_cache(sql: str, force_refresh: bool = False) -> pd.DataFrame:
    cache_path = get_cache_path(sql)

    if cache_path.exists() and not force_refresh:
        return pd.read_parquet(cache_path)

    df = run_athena_query_to_dataframe(sql)
    df.to_parquet(cache_path, index=False)
    return df


def load_student_scores_from_athena(
    event_id: str,
    subject: str,
    parentpanel_prefix: str,
    force_refresh: bool = False,
) -> pd.DataFrame:
    sql = build_student_score_sql(
        event_id=event_id,
        subject=subject,
        parentpanel_prefix=parentpanel_prefix,
    )

    return load_athena_with_cache(sql, force_refresh=force_refresh)


def build_daily_s3_prefix(
    base_prefix: str = "fzhao/Unsaved",
    target_date: date | None = None,
) -> str:
    target_date = target_date or date.today()

    return (
        f"{base_prefix.strip('/')}/"
        f"{target_date.year:04d}/"
        f"{target_date.month:02d}/"
        f"{target_date.day:02d}/"
    )


def list_s3_objects(bucket: str, prefix: str) -> list[dict]:
    s3 = get_s3_client()
    paginator = s3.get_paginator("list_objects_v2")

    objects = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith("/"):
                objects.append(obj)

    return objects


def list_s3_common_prefixes(bucket: str, prefix: str) -> list[str]:
    s3 = get_s3_client()
    paginator = s3.get_paginator("list_objects_v2")

    prefixes = []

    for page in paginator.paginate(
        Bucket=bucket,
        Prefix=prefix.strip("/") + "/",
        Delimiter="/",
    ):
        for item in page.get("CommonPrefixes", []):
            prefixes.append(item["Prefix"])

    return sorted(prefixes)


def list_s3_data_files(bucket: str, prefix: str) -> list[str]:
    objects = list_s3_objects(bucket, prefix)

    # Return all non-folder objects.
    # Athena result files may not always match expected extensions.
    keys = [
        obj["Key"]
        for obj in objects
        if not obj["Key"].endswith("/")
    ]

    return sorted(keys)


def read_s3_data_file(bucket: str, key: str) -> pd.DataFrame:
    s3 = get_s3_client()
    obj = s3.get_object(Bucket=bucket, Key=key)
    data = obj["Body"].read()

    key_lower = key.lower()

    if key_lower.endswith(".parquet"):
        return coerce_numeric_columns(pd.read_parquet(BytesIO(data)))

    if key_lower.endswith(".tsv") or key_lower.endswith(".txt"):
        return coerce_numeric_columns(pd.read_csv(BytesIO(data), sep="\t"))

    # Default: try CSV. Athena results are usually CSV-like.
    return coerce_numeric_columns(pd.read_csv(BytesIO(data)))


def read_latest_s3_data_file(bucket: str, prefix: str) -> pd.DataFrame:
    objects = list_s3_objects(bucket, prefix)

    data_objects = [
        obj
        for obj in objects
        if obj["Key"].lower().endswith(VALID_DATA_EXTENSIONS)
    ]

    if not data_objects:
        raise FileNotFoundError(f"No data files found under s3://{bucket}/{prefix}")

    latest_obj = max(data_objects, key=lambda obj: obj["LastModified"])
    return read_s3_data_file(bucket, latest_obj["Key"])


def find_latest_s3_data_prefix(
    bucket: str,
    base_prefix: str = "fzhao/Unsaved",
) -> str:
    base_prefix = base_prefix.strip("/") + "/"

    year_prefixes = list_s3_common_prefixes(bucket, base_prefix)
    if not year_prefixes:
        raise FileNotFoundError(f"No year folders found under s3://{bucket}/{base_prefix}")

    latest_year = sorted(year_prefixes)[-1]

    month_prefixes = list_s3_common_prefixes(bucket, latest_year)
    if not month_prefixes:
        raise FileNotFoundError(f"No month folders found under s3://{bucket}/{latest_year}")

    latest_month = sorted(month_prefixes)[-1]

    day_prefixes = list_s3_common_prefixes(bucket, latest_month)
    if not day_prefixes:
        raise FileNotFoundError(f"No day folders found under s3://{bucket}/{latest_month}")

    latest_day = sorted(day_prefixes)[-1]

    return latest_day


def find_latest_s3_data_file(
    bucket: str,
    base_prefix: str = "fzhao/Unsaved",
) -> str:
    latest_prefix = find_latest_s3_data_prefix(bucket, base_prefix)
    objects = list_s3_objects(bucket, latest_prefix)

    data_objects = [
        obj
        for obj in objects
        if obj["Key"].lower().endswith(VALID_DATA_EXTENSIONS)
    ]

    if not data_objects:
        raise FileNotFoundError(f"No data files found under s3://{bucket}/{latest_prefix}")

    latest_obj = max(data_objects, key=lambda obj: obj["LastModified"])
    return latest_obj["Key"]

def start_athena_query(sql: str, output_location: str) -> str:
    session = get_active_session()
    if session is None:
        raise RuntimeError("No AWS session found. Please assume role first.")

    athena = session.client("athena")

    response = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": "pine_msmstscoring_database"},
        ResultConfiguration={
            "OutputLocation": output_location,
        },
        WorkGroup="primary",
    )

    return response["QueryExecutionId"]


def get_query_status(query_execution_id: str) -> dict:
    athena = get_athena_client()
    resp = athena.get_query_execution(QueryExecutionId=query_execution_id)
    return resp["QueryExecution"]["Status"]


def get_query_output_location(query_execution_id: str) -> str | None:
    athena = get_athena_client()
    resp = athena.get_query_execution(QueryExecutionId=query_execution_id)
    return resp["QueryExecution"]["ResultConfiguration"].get("OutputLocation")


def read_athena_result_by_id(query_execution_id: str) -> pd.DataFrame:
    s3_uri = get_query_output_location(query_execution_id)
    if not s3_uri:
        raise RuntimeError("No OutputLocation found for this query.")

    bucket, key = parse_s3_uri(s3_uri)
    return read_s3_data_file(bucket=bucket, key=key)


def start_athena_query(sql: str) -> str:
    aws = st.secrets["aws"]
    athena = get_athena_client()

    response = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={
            "Database": aws.get("athena_database", DEFAULT_DATABASE),
        },
        WorkGroup=aws.get("athena_workgroup", "primary"),
    )

    return response["QueryExecutionId"]


def get_query_status(query_execution_id: str) -> dict:
    athena = get_athena_client()
    response = athena.get_query_execution(QueryExecutionId=query_execution_id)
    return response["QueryExecution"]["Status"]


def get_query_output_location(query_execution_id: str) -> str | None:
    athena = get_athena_client()
    response = athena.get_query_execution(QueryExecutionId=query_execution_id)
    return response["QueryExecution"]["ResultConfiguration"].get("OutputLocation")


def read_athena_result_by_id(query_execution_id: str) -> pd.DataFrame:
    s3_uri = get_query_output_location(query_execution_id)

    if not s3_uri:
        raise RuntimeError("No OutputLocation found for this Athena query.")

    bucket, key = parse_s3_uri(s3_uri)
    return read_s3_data_file(bucket=bucket, key=key)