"""User data and template loading for EC2 launch."""

from . import config

_OFELIA_SCHEDULE_PARTS = 4
_CRON_SCHEDULE_PARTS = 6


def _ofelia_to_cron(ofelia_schedule: str) -> str:
    """Convert ofelia schedule to cron format.

    Converts (sec min hour day month wday) to (min hour day month wday).
    Used by build_user_data() for CRON_CIK and CRON_CUSIP passed to user_data template.
    """
    parts = ofelia_schedule.split()
    if len(parts) >= _OFELIA_SCHEDULE_PARTS:
        return f"{parts[1]} {parts[2]} * * *"  # min hour day month wday
    return "0 2 * * *"  # default 02:00


def _parse_cron(raw: str | None, default_ofelia: str) -> str:
    """Parse cron from config: accept cron format (min hour day month wday) or ofelia (sec min hour day month wday)."""
    if not raw or not raw.strip():
        return _ofelia_to_cron(default_ofelia)
    parts = raw.strip().split()
    if len(parts) >= _CRON_SCHEDULE_PARTS:
        return _ofelia_to_cron(raw)
    return raw.strip()


def _load_template(name: str, **replacements: str) -> str:
    """Load a template file and apply string replacements."""
    path = config.TEMPLATES_DIR / name
    content = path.read_text()
    for key, value in replacements.items():
        content = content.replace("{" + key + "}", value)
    return content


def build_user_data(
    name_prefix: str,
    orch_img: str,
    processor_bucket: str,
    use_s3_output: bool = True,
) -> str:
    """Build EC2 user data script from templates (matches .env.example structure)."""
    secret_retrieval = _load_template(
        "secret_retrieval_secrets_manager.sh",
        name_prefix=name_prefix,
    )

    compose_content = config.COMPOSE_PATH.read_text()

    # ECR registry for pull-and-run script (host cron pulls before each run)
    ecr_registry = orch_img.split("/")[0] if "/" in orch_img else ""
    pull_and_run_script = _load_template(
        "pull_and_run.sh.template",
        ECR_REGISTRY=ecr_registry,
        AWS_REGION=config.aws_region,
    )

    cron_cik = _parse_cron(config.config.get("cron_cik"), "0 0 2 * * *")
    cron_cusip = _parse_cron(config.config.get("cron_cusip"), "0 30 2 * * *")

    output_dir = (
        f"s3://{processor_bucket}/output/" if use_s3_output else "/home/ec2-user/data/output"
    )

    default_input = f"s3://{processor_bucket}/input/data.parquet"
    input_cik = config.config.get("input_file_cik") or default_input
    input_cusip = config.config.get("input_file_cusip") or default_input
    input_cik_match = config.config.get("input_file_cik_match") or default_input
    input_ticker = config.config.get("input_file_ticker") or default_input

    return _load_template(
        "user_data.sh.template",
        SECRET_RETRIEVAL=secret_retrieval,
        COMPOSE_DELIM="COMPOSE_END",
        COMPOSE_CONTENT=compose_content,
        ORCHESTRATOR_IMAGE=orch_img,
        PULL_AND_RUN_SCRIPT=pull_and_run_script,
        CRON_CIK=cron_cik,
        CRON_CUSIP=cron_cusip,
        OUTPUT_DIR=output_dir,
        AWS_REGION=config.aws_region,
        INPUT_FILE_CIK=input_cik,
        INPUT_FILE_CUSIP=input_cusip,
        INPUT_FILE_CIK_MATCH=input_cik_match,
        INPUT_FILE_TICKER=input_ticker,
    )
