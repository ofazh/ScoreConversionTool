from datetime import datetime
import os

import boto3
import streamlit as st


def get_verify_setting():
    ca_bundle = os.environ.get("AWS_CA_BUNDLE")
    if ca_bundle:
        return ca_bundle

    try:
        aws = st.secrets.get("aws", {})
        if "ca_bundle" in aws:
            return aws["ca_bundle"]
        if "ssl_verify" in aws:
            return bool(aws["ssl_verify"])
    except Exception:
        pass

    return True


def assume_role_with_mfa(
    source_profile: str,
    role_arn: str,
    mfa_serial: str,
    token_code: str,
    region_name: str = "us-east-1",
    duration_seconds: int = 43200,
):
    base_session = boto3.Session(
        profile_name=source_profile,
        region_name=region_name,
    )

    sts = base_session.client("sts", verify=get_verify_setting())

    session_id = datetime.now().strftime("%Y%m%d%H%M%S")

    response = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"session-{session_id}-streamlit",
        DurationSeconds=duration_seconds,
        SerialNumber=mfa_serial,
        TokenCode=token_code,
    )

    creds = response["Credentials"]

    assumed_session = boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region_name,
    )

    return assumed_session, creds


def aws_assume_role_panel():
    st.subheader("AWS Authentication")

    try:
        aws = st.secrets.get("aws", {})
    except Exception:
        aws = {}

    default_source_profile = aws.get("source_profile", "cb-cia-admin-prod")
    default_region = aws.get("region_name", "us-east-1")
    default_role_arn = aws.get("role_arn", "")
    default_mfa_serial = aws.get("mfa_serial", "")

    source_profile = st.text_input(
        "Source AWS profile",
        value=default_source_profile,
    )

    role_arn = st.text_input(
        "Role ARN",
        value=default_role_arn,
        type="password",
    )

    mfa_serial = st.text_input(
        "MFA serial",
        value=default_mfa_serial,
        type="password",
    )

    token_code = st.text_input(
        "Enter MFA code",
        max_chars=6,
        type="password",
    )

    duration_hours = st.number_input(
        "Credential duration in hours",
        min_value=1,
        max_value=12,
        value=12,
    )

    if st.button("Assume AWS Role"):
        if not source_profile or not role_arn or not mfa_serial or not token_code:
            st.error("Source profile, role ARN, MFA serial, and MFA code are required.")
        else:
            try:
                session, creds = assume_role_with_mfa(
                    source_profile=source_profile,
                    role_arn=role_arn,
                    mfa_serial=mfa_serial,
                    token_code=token_code,
                    region_name=default_region,
                    duration_seconds=int(duration_hours * 3600),
                )

                st.session_state["aws_boto3_session"] = session
                st.session_state["aws_credentials_expiration"] = creds["Expiration"]

                st.success(
                    f"AWS role assumed successfully. Credentials expire at {creds['Expiration']}."
                )

            except Exception as exc:
                st.error(f"Failed to assume AWS role: {exc}")

    if st.session_state.get("aws_boto3_session") is not None:
        st.success(
            f"AWS session is active. Expires at {st.session_state.get('aws_credentials_expiration')}."
        )

st.subheader("AWS Session Debug")

if st.button("Debug AWS session"):
    try:
        session = st.session_state.get("aws_boto3_session")

        if session is None:
            st.error("No aws_boto3_session in session_state. Click Assume AWS Role first.")
        else:
            sts = session.client("sts")
            identity = sts.get_caller_identity()
            st.write("Active identity:")
            st.json(identity)

            s3 = session.client("s3")
            resp = s3.list_objects_v2(
                Bucket="msmstsnapshot-analysis-results-prod",
                Prefix="OP_PT_SAT_202512_WK_AM_12082025/irt/ANCHOR/EBRW/",
                MaxKeys=10,
            )

            st.write("S3 list response:")
            st.json(resp)

    except Exception as exc:
        st.error(f"Debug failed: {exc}")

