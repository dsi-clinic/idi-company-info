"""User data and template loading for EC2 launch."""

import yaml

from . import config


def _load_compose_for_ec2() -> str:
    """Load docker-compose.yml and remove build blocks (EC2 has no source, pulls from ECR)."""
    compose = yaml.safe_load(config.COMPOSE_PATH.read_text())
    services = compose.get("services", {})
    ec2_services = (
        "scheduler",
        "orchestrator-cik",
        "orchestrator-cusip",
        "orchestrator-cik-match",
        "orchestrator-ticker",
    )
    for svc in ec2_services:
        if svc in services and "build" in services[svc]:
            del services[svc]["build"]
    return yaml.dump(compose, default_flow_style=False, sort_keys=False)


def _load_template(name: str, **replacements: str) -> str:
    """Load a template file and apply string replacements."""
    path = config.TEMPLATES_DIR / name
    content = path.read_text()
    for key, value in replacements.items():
        content = content.replace("{" + key + "}", value)
    return content


def build_user_data(name_prefix: str, has_secrets: bool, orch_img: str, sched_img: str) -> str:
    """Build EC2 user data script from templates (matches .env.example structure)."""
    if has_secrets:
        secret_retrieval = _load_template(
            "secret_retrieval_secrets_manager.sh",
            name_prefix=name_prefix,
        )
    else:
        secret_retrieval = _load_template("secret_retrieval_placeholders.sh")

    compose_content = _load_compose_for_ec2()

    return _load_template(
        "user_data.sh.template",
        SECRET_RETRIEVAL=secret_retrieval,
        COMPOSE_DELIM="COMPOSE_END",
        COMPOSE_CONTENT=compose_content,
        ORCHESTRATOR_IMAGE=orch_img,
        SCHEDULER_IMAGE=sched_img,
    )
