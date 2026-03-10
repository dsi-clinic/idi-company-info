"""User data and template loading for EC2 launch."""

import yaml

from . import config


def _ofelia_to_cron(ofelia_schedule: str) -> str:
    """Convert ofelia schedule (sec min hour day month wday) to cron (min hour day month wday).
    Used by build_user_data() for CRON_CIK and CRON_CUSIP passed to user_data template."""
    parts = ofelia_schedule.split()
    if len(parts) >= 4:
        return f"{parts[1]} {parts[2]} * * *"  # min hour day month wday
    return "0 2 * * *"  # default 02:00


def _parse_cron(raw: str | None, default_ofelia: str) -> str:
    """Parse cron from config: accept cron format (min hour day month wday) or ofelia (sec min hour day month wday)."""
    if not raw or not raw.strip():
        return _ofelia_to_cron(default_ofelia)
    parts = raw.strip().split()
    if len(parts) >= 6:
        return _ofelia_to_cron(raw)
    return raw.strip()


def _load_compose_for_ec2() -> str:
    """Load docker-compose.yml for EC2: remove build blocks (EC2 pulls from ECR)."""
    compose = yaml.safe_load(config.COMPOSE_PATH.read_text())
    services = compose.get("services", {})
    for svc in services:
        if "build" in services[svc]:
            del services[svc]["build"]
    return yaml.dump(compose, default_flow_style=False, sort_keys=False)


def _load_template(name: str, **replacements: str) -> str:
    """Load a template file and apply string replacements."""
    path = config.TEMPLATES_DIR / name
    content = path.read_text()
    for key, value in replacements.items():
        content = content.replace("{" + key + "}", value)
    return content


def build_user_data(
    name_prefix: str,
    has_secrets: bool,
    orch_img: str,
    processor_bucket: str,
    use_s3_output: bool = True,
) -> str:
    """Build EC2 user data script from templates (matches .env.example structure)."""
    if has_secrets:
        secret_retrieval = _load_template(
            "secret_retrieval_secrets_manager.sh",
            name_prefix=name_prefix,
        )
    else:
        secret_retrieval = _load_template("secret_retrieval_placeholders.sh")

    compose_content = _load_compose_for_ec2()

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
    )
