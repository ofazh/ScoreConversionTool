import os
import re
from datetime import datetime
from io import BytesIO

import boto3
import pandas as pd
import streamlit as st

from core.io import read_uploaded_table
from core.prepare import (
    infer_section_from_name,
    infer_test_from_name,
    standardize_student_columns,
    standardize_conversion_columns,
)
from core.validation import validate_conversion_table, validate_student_file


os.environ["AWS_CA_BUNDLE"] = r"C:\Users\fzhao\.aws\Zscaler-AWS.pem"


# -----------------------------
# General helpers
# -----------------------------
def safe_key(*parts) -> str:
    raw = "_".join(str(p) for p in parts)
    return re.sub(r"[^a-zA-Z0-9_]+", "_", raw)


def clean_method_name(file_name: str, sheet_name: str) -> str:
    name = f"{file_name}_{sheet_name}"
    for suffix in [".xlsx", ".xls", ".csv", ".sas7bdat", ".xpt", ".rds"]:
        name = name.replace(suffix, "").replace(suffix.upper(), "")
    return name


def read_conversion_workbook(file) -> dict[str, pd.DataFrame]:
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


def enforce_conversion_precision(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    out["theta_min"] = pd.to_numeric(out["theta_min"], errors="coerce").round(6)
    out["theta_max"] = pd.to_numeric(out["theta_max"], errors="coerce").round(6)
    out["scale_score"] = pd.to_numeric(out["scale_score"], errors="coerce")

    return out


# -----------------------------
# AWS helpers
# -----------------------------
def assume_role_with_mfa(
    source_profile: str,
    role_arn: str,
    mfa_serial: str,
    token: str,
    region_name: str = "us-east-1",
):
    base_session = boto3.Session(profile_name=source_profile, region_name=region_name)
    sts = base_session.client("sts")

    session_id = datetime.now().strftime("%Y%m%d%H%M%S")

    response = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"session-{session_id}-streamlit",
        DurationSeconds=3600 * 12,
        SerialNumber=mfa_serial,
        TokenCode=token,
    )

    creds = response["Credentials"]

    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region_name,
    ), creds


def get_active_session():
    return st.session_state.get("aws_boto3_session")


def check_active_aws_identity():
    session = get_active_session()

    if session is None:
        st.error("No AWS session found. Please assume role first.")
        return

    try:
        sts = session.client("sts")
        st.json(sts.get_caller_identity())
    except Exception as exc:
        st.error(f"Failed to check identity: {exc}")


def list_s3_keys(bucket: str, prefix: str) -> list[str]:
    session = get_active_session()
    if session is None:
        raise RuntimeError("No AWS session found. Please assume role first.")

    s3 = session.client("s3")

    keys = []
    token = None

    while True:
        kwargs = {
            "Bucket": bucket,
            "Prefix": prefix,
            "MaxKeys": 1000,
        }

        if token:
            kwargs["ContinuationToken"] = token

        response = s3.list_objects_v2(**kwargs)

        for obj in response.get("Contents", []):
            key = obj["Key"]
            if not key.endswith("/"):
                keys.append(key)

        if not response.get("IsTruncated"):
            break

        token = response.get("NextContinuationToken")

    return keys


def read_s3_object_to_df(bucket: str, key: str) -> pd.DataFrame:
    session = get_active_session()
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

    if key_lower.endswith(".sas7bdat"):
        return pd.read_sas(BytesIO(data), format="sas7bdat", encoding="latin1")

    if key_lower.endswith(".xpt"):
        return pd.read_sas(BytesIO(data), format="xport", encoding="latin1")

    return pd.read_csv(BytesIO(data))


def build_student_score_sql(
    event_id: str,
    subject: str,
    parentpanel_prefix: str,
) -> str:
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
FROM "pine_msmstscoring_database"."pine_sas_student_scr_v"
WHERE event_id IN ('{event_id}')
  AND subject IN ('{subject}')
  AND substring(parentpanelid, 1, 1) IN ('{parentpanel_prefix}');
""".strip()


def build_athena_output_location(
    base_s3_uri: str = "s3://pine-msmstscoring-bucket-athena-view/fzhao/Unsaved",
) -> str:
    now = datetime.now()

    return (
        f"{base_s3_uri.rstrip('/')}/"
        f"{now.year:04d}/"
        f"{now.month:02d}/"
        f"{now.day:02d}/"
    )


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
    session = get_active_session()
    if session is None:
        raise RuntimeError("No AWS session found. Please assume role first.")

    athena = session.client("athena")
    response = athena.get_query_execution(QueryExecutionId=query_execution_id)
    return response["QueryExecution"]["Status"]


def read_athena_result_by_id(query_execution_id: str) -> pd.DataFrame:
    session = get_active_session()
    if session is None:
        raise RuntimeError("No AWS session found. Please assume role first.")

    athena = session.client("athena")
    response = athena.get_query_execution(QueryExecutionId=query_execution_id)

    output_location = response["QueryExecution"]["ResultConfiguration"].get("OutputLocation")
    if not output_location:
        raise RuntimeError("No Athena OutputLocation found.")

    s3_uri = output_location.replace("s3://", "")
    bucket, key = s3_uri.split("/", 1)

    return read_s3_object_to_df(bucket=bucket, key=key)


# -----------------------------
# Main page
# -----------------------------
st.title("Upload and Validate")

# ============================================================
# Persistent session state initialization
# ============================================================
DEFAULT_SESSION_KEYS = {
    "combined_conversion_df": None,
    "conversion_validations": {},
    "table_name_map": {},
    "table_section_map": {},
    "table_test_map": {},
    "raw_student_df": None,
    "student_df": None,
    "student_validation": None,
    "column_mapping": None,
    "student_data_source": None,
    "s3_folder_files": [],
    "last_query_id": None,
    "last_query_status": None,
}

for k, v in DEFAULT_SESSION_KEYS.items():
    if k not in st.session_state:
        st.session_state[k] = v

if st.button("Reset uploaded conversion/student data", key="reset_upload_page"):
    for key in DEFAULT_SESSION_KEYS.keys():
        st.session_state.pop(key, None)

    st.rerun()
# ============================================================
# 1. Conversion table section
# ============================================================
st.header("1. Upload Conversion Tables")

conversion_files = st.file_uploader(
    "Upload one or more conversion tables",
    type=["csv", "xlsx", "xls", "sas7bdat", "xpt", "rds"],
    accept_multiple_files=True,
    key="conversion_uploader",
)

if conversion_files:
    all_tables = []
    validation_map = dict(st.session_state["conversion_validations"])
    table_name_map = dict(st.session_state["table_name_map"])
    table_section_map = dict(st.session_state["table_section_map"])
    table_test_map = dict(st.session_state["table_test_map"])

    st.subheader("Configure conversion tables")

    if conversion_files:
        st.session_state["persisted_conversion_files"] = conversion_files

    elif st.session_state.get("persisted_conversion_files"):
        conversion_files = st.session_state["persisted_conversion_files"]

    for file in conversion_files:
        try:
            sheet_map = read_conversion_workbook(file)
        except Exception as exc:
            st.error(f"Failed to read {file.name}: {exc}")
            continue

        available_sheets = list(sheet_map.keys())

        st.markdown(f"### {file.name}")
        st.info(f"Found {len(available_sheets)} sheet/table(s): {available_sheets}")

        selected_sheets = st.multiselect(
            "Select workbook tab(s) to import",
            options=available_sheets,
            default=available_sheets,
            key=safe_key("selected_sheets", file.name),
        )

        st.session_state[safe_key("persist_selected_sheets", file.name)] = selected_sheets

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

                    st.success(f"Prepared: {method_name} | {content_area} | {test_name}")

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

if st.session_state.get("combined_conversion_df") is not None:
    st.success("Previously loaded conversion tables are active.")

    st.dataframe(
        st.session_state["combined_conversion_df"].head(100).style.format(
            {
                "theta_min": "{:.6f}",
                "theta_max": "{:.6f}",
            }
        ),
        use_container_width=True,
    )
# ============================================================
# 2. Student data source section
# ============================================================
st.header("2. Student Data Source")

student_source = st.radio(
    "Choose student data source",
    ["Upload file", "AWS / S3 / Athena"],
    horizontal=True,
    key="student_source",
)

raw_student_df = st.session_state["raw_student_df"]

if student_source == "Upload file":
    student_file = st.file_uploader(
        "Upload student score file",
        type=["csv", "xlsx", "xls", "sas7bdat", "xpt", "rds"],
        key="student_uploader",
    )

    if student_file is not None:
        try:
            raw_student_df = read_uploaded_table(student_file)
            st.session_state["raw_student_df"] = raw_student_df
            st.session_state["student_data_source"] = "upload"
            st.success(f"Uploaded {len(raw_student_df):,} rows.")
        except Exception as exc:
            st.error(f"Failed to read uploaded student file: {exc}")

else:
    aws_tab, s3_folder_tab, exact_key_tab, athena_tab = st.tabs(
        [
            "AWS Authentication",
            "Load from S3 Folder",
            "Load Exact S3 Key",
            "Run Athena Query (DNU)",
        ]
    )

    with aws_tab:
        st.subheader("AWS Authentication")

        source_profile = st.text_input(
            "Source profile",
            value="cb-cia-admin-prod",
            key="aws_source_profile",
        )

        role_arn = st.text_input(
            "Role ARN",
            value="arn:aws:iam::***************:role/sas-athena-odbc-access-prod-role",
            type="password",
            key="aws_role_arn",
        )

        mfa_serial = st.text_input(
            "MFA serial",
            value="arn:aws:iam::***************:mfa/fzhao",
            type="password",
            key="aws_mfa_serial",
        )

        mfa_token = st.text_input(
            "MFA token",
            type="password",
            max_chars=6,
            key="aws_mfa_token",
        )

        if st.button("Assume AWS role", type="primary", key="assume_aws_role"):
            try:
                session, creds = assume_role_with_mfa(
                    source_profile=source_profile,
                    role_arn=role_arn,
                    mfa_serial=mfa_serial,
                    token=mfa_token,
                )

                st.session_state["aws_boto3_session"] = session
                st.session_state["aws_credentials_expiration"] = creds["Expiration"]

                st.success(f"AWS role assumed. Credentials expire at {creds['Expiration']}.")

            except Exception as exc:
                st.error(f"Failed to assume AWS role: {exc}")

        if st.button("Check active AWS identity", key="check_aws_identity"):
            check_active_aws_identity()

        st.write("Credentials expiration:", st.session_state.get("aws_credentials_expiration"))

    with s3_folder_tab:
        st.subheader("Load student data from S3 folder")

        bucket = st.text_input(
            "S3 bucket",
            value="pine-msmstscoring-bucket-athena-view",
            key="s3_folder_bucket",
        )

        eventid = st.text_input("Top-level folder", value="fzhao", key="s3_eventid")
        testadmin = st.text_input("Result folder", value="Unsaved", key="s3_testadmin")
        year = st.text_input("Year folder", value="2026", key="s3_year")
        month = st.text_input("Month folder", value="05", key="s3_month")
        day = st.text_input("Day folder", value="13", key="s3_day")

        prefix = (
            f"{eventid.strip('/')}/"
            f"{testadmin.strip('/')}/"
            f"{year.strip('/')}/"
            f"{month.strip('/')}/"
            f"{day.strip('/')}/"
        )

        st.write(f"Prefix: `s3://{bucket}/{prefix}`")

        if st.button("List files in S3 folder", key="list_s3_folder_files"):
            try:
                files = list_s3_keys(bucket=bucket, prefix=prefix)
                st.session_state["s3_folder_files"] = files

                if files:
                    st.success(f"Found {len(files)} file(s).")
                else:
                    st.warning("No files found under this prefix.")
            except Exception as exc:
                st.error(f"Failed to list S3 files: {exc}")

        files = st.session_state.get("s3_folder_files", [])

        if files:
            selected_key = st.selectbox(
                "Select S3 file",
                options=files,
                key="selected_s3_folder_file",
            )

            if st.button("Load selected S3 file", type="primary", key="load_s3_folder_file"):
                try:
                    raw_student_df = read_s3_object_to_df(bucket=bucket, key=selected_key)

                    st.session_state["raw_student_df"] = raw_student_df
                    st.session_state["student_data_source"] = "s3_folder"
                    st.session_state["selected_s3_bucket"] = bucket
                    st.session_state["selected_s3_key"] = selected_key

                    st.success(f"Loaded {len(raw_student_df):,} rows from S3.")
                except Exception as exc:
                    st.error(f"Failed to load selected S3 file: {exc}")

        if st.session_state.get("student_data_source") == "s3_folder":
            if st.session_state.get("raw_student_df") is not None:
                raw_student_df = st.session_state["raw_student_df"]
                st.subheader("S3-loaded student data preview")
                st.dataframe(raw_student_df.head(25), use_container_width=True)

    with exact_key_tab:
        st.subheader("Load exact S3 key")

        bucket = st.text_input(
            "S3 bucket",
            value="pine-msmstscoring-bucket-athena-view",
            key="exact_s3_bucket",
        )

        exact_key = st.text_input(
            "Exact S3 key",
            value="fzhao/Unsaved/2026/05/13/<actual_file_name>",
            key="exact_s3_key",
        )

        st.caption("Do not include `s3://` or `arn:aws:s3:::`. Key must include the file name.")

        if st.button("Load exact S3 key", type="primary", key="load_exact_s3_key"):
            try:
                raw_student_df = read_s3_object_to_df(
                    bucket=bucket,
                    key=exact_key.strip(),
                )

                st.session_state["raw_student_df"] = raw_student_df
                st.session_state["student_data_source"] = "s3_exact_key"
                st.session_state["selected_s3_bucket"] = bucket
                st.session_state["selected_s3_key"] = exact_key.strip()

                st.success(f"Loaded {len(raw_student_df):,} rows from S3.")
            except Exception as exc:
                st.error(f"Failed to load exact S3 key: {exc}")

        if st.session_state.get("student_data_source") == "s3_exact_key":
            if st.session_state.get("raw_student_df") is not None:
                raw_student_df = st.session_state["raw_student_df"]
                st.subheader("Exact-key student data preview")
                st.dataframe(raw_student_df.head(25), use_container_width=True)

    with athena_tab:
        st.subheader("Run Athena query")

        st.warning(
            "Athena query submission requires a writable query-result S3 location. "
            "If the role lacks s3:PutObject, submission will fail."
        )

        event_id = st.text_input("EVENT_ID", value="####", key="athena_event_id")

        subject = st.selectbox(
            "Subject",
            ["math", "reading"],
            key="athena_subject",
        )

        parentpanel_prefix = st.selectbox(
            "Administration type / parentpanelid prefix",
            ["W", "S", "I", "P", "V"],
            format_func=lambda x: {
                "W": "W - Weekend",
                "S": "S - School Day",
                "I": "I - International",
                "P": "P - P89/P10",
                "V": "V - Custom/Other",
            }[x],
            key="athena_parentpanel_prefix",
        )

        sql_default = build_student_score_sql(
            event_id=event_id,
            subject=subject,
            parentpanel_prefix=parentpanel_prefix,
        )

        sql = st.text_area(
            "Athena SQL",
            value=sql_default,
            height=260,
            key="athena_sql_editor",
        )

        athena_base_output = st.text_input(
            "Athena output base S3 location",
            value="s3://pine-msmstscoring-bucket-athena-view/fzhao/Unsaved",
            key="athena_base_output",
        )

        athena_output_location = build_athena_output_location(athena_base_output)

        st.info(f"Query results will be written to: `{athena_output_location}`")

        if st.button("Submit Athena query", type="primary", key="submit_athena_query"):
            try:
                qid = start_athena_query(sql, athena_output_location)
                st.session_state["last_query_id"] = qid
                st.success(f"Query submitted. QueryExecutionId: {qid}")
            except Exception as exc:
                st.error(f"Failed to submit Athena query: {exc}")

        qid = st.session_state.get("last_query_id")

        if qid:
            st.info(f"Last QueryExecutionId: `{qid}`")

            col1, col2 = st.columns(2)

            with col1:
                if st.button("Check query status", key="check_athena_status"):
                    try:
                        status = get_query_status(qid)
                        st.session_state["last_query_status"] = status
                    except Exception as exc:
                        st.error(f"Failed to check query status: {exc}")

            with col2:
                if st.button("Load result from last query", key="load_athena_result"):
                    try:
                        raw_student_df = read_athena_result_by_id(qid)

                        st.session_state["raw_student_df"] = raw_student_df
                        st.session_state["student_data_source"] = "athena_query_result"

                        st.success(f"Loaded {len(raw_student_df):,} rows from Athena result.")
                    except Exception as exc:
                        st.error(f"Failed to load Athena result: {exc}")

            status = st.session_state.get("last_query_status")
            if status:
                st.write(status)

        if st.session_state.get("student_data_source") == "athena_query_result":
            if st.session_state.get("raw_student_df") is not None:
                raw_student_df = st.session_state["raw_student_df"]
                st.subheader("Athena-loaded student data preview")
                st.dataframe(raw_student_df.head(25), use_container_width=True)


# ============================================================
# 3. Student mapping and validation
# ============================================================
if raw_student_df is not None:
    try:
        st.header("3. Map and Validate Student Data")

        st.subheader("Raw student data preview")
        st.dataframe(raw_student_df.head(25), use_container_width=True)

        st.write(f"Student data rows: **{len(raw_student_df):,}**")
        st.write(f"Student data columns: **{len(raw_student_df.columns):,}**")

        student_columns = raw_student_df.columns.tolist()
        lower_to_original = {str(c).lower(): c for c in student_columns}

        def default_index(preferred_names, fallback=0):
            for name in preferred_names:
                if name.lower() in lower_to_original:
                    return student_columns.index(lower_to_original[name.lower()])
            return fallback

        def optional_default_index(preferred_names):
            optional_cols_local = ["<None>"] + student_columns
            lower_optional = {str(c).lower(): c for c in optional_cols_local}
            for name in preferred_names:
                if name.lower() in lower_optional:
                    return optional_cols_local.index(lower_optional[name.lower()])
            return 0

        student_id_col = st.selectbox(
            "Student ID column",
            student_columns,
            index=default_index(["PERSON_ID", "person_id"]),
            key="map_student_id",
        )

        section_col = st.selectbox(
            "Section / Subject column",
            student_columns,
            index=default_index(["SUBJECT", "subject"]),
            key="map_section",
        )

        theta_candidates = [c for c in student_columns if "theta" in str(c).lower()]
        default_theta = (
            lower_to_original["theta_eap"]
            if "theta_eap" in lower_to_original
            else (theta_candidates[0] if theta_candidates else student_columns[0])
        )

        theta_col = st.selectbox(
            "Theta column",
            student_columns,
            index=student_columns.index(default_theta),
            key="map_theta",
        )

        optional_cols = ["<None>"] + student_columns

        event_id_col = st.selectbox(
            "Event ID column (optional)",
            optional_cols,
            index=optional_default_index(["EVENT_ID", "event_id"]),
            key="map_event_id",
        )

        num_correct_col = st.selectbox(
            "Number correct column (optional)",
            optional_cols,
            index=optional_default_index(["NUM_CORRECT", "num_correct"]),
            key="map_num_correct",
        )

        production_score_col = st.selectbox(
            "Production scale score column (optional)",
            optional_cols,
            index=optional_default_index(["Scaled_Score", "scaled_score"]),
            key="map_production_score",
        )

        required_selected = [student_id_col, section_col, theta_col]
        if len(set(required_selected)) < 3:
            st.error("Student ID, Section, and Theta must map to three different columns.")
            st.stop()

        column_mapping = {
            "student_id": student_id_col,
            "section": section_col,
            "theta": theta_col,
            "event_id": None if event_id_col == "<None>" else event_id_col,
            "num_correct": None if num_correct_col == "<None>" else num_correct_col,
            "production_scalescore": None
            if production_score_col == "<None>"
            else production_score_col,
        }

        standardized_student_df = standardize_student_columns(raw_student_df, column_mapping)
        student_validation = validate_student_file(standardized_student_df)

        st.session_state["column_mapping"] = column_mapping
        st.session_state["student_df"] = standardized_student_df
        st.session_state["student_validation"] = student_validation

        st.subheader("Standardized student data preview")
        st.dataframe(standardized_student_df.head(25), use_container_width=True)

        with st.expander("Show mapping details", expanded=False):
            st.write("Student data source:", st.session_state.get("student_data_source"))
            st.write("Selected S3 bucket:", st.session_state.get("selected_s3_bucket"))
            st.write("Selected S3 key:", st.session_state.get("selected_s3_key"))
            st.write("Raw columns:", student_columns)
            st.write("Column mapping:", column_mapping)
            st.write("Standardized columns:", standardized_student_df.columns.tolist())

        st.subheader("Student data validation")
        for msg in student_validation.errors:
            st.error(msg)
        for msg in student_validation.warnings:
            st.warning(msg)
        for msg in student_validation.infos:
            st.info(msg)

    except Exception as exc:
        st.error(f"Failed to process student data: {exc}")


# ============================================================
# 4. Overall Status
# ============================================================
st.header("4. Overall Status")

student_valid = (
    st.session_state.get("student_validation") is not None
    and st.session_state["student_validation"].is_valid
)

conv_valid = False
if st.session_state.get("conversion_validations"):
    conv_valid = all(
        (not isinstance(v, Exception)) and v.is_valid
        for v in st.session_state["conversion_validations"].values()
    )

table_name_map = st.session_state.get("table_name_map", {})
if table_name_map and len(table_name_map.values()) != len(set(table_name_map.values())):
    conv_valid = False

if student_valid and conv_valid:
    st.success("All uploaded files passed validation checks. You can proceed to comparison.")
else:
    st.info("Upload data and resolve any validation errors before proceeding.")