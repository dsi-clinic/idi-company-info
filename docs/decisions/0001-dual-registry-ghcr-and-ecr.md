# Dual Container Registry: GHCR (public) and ECR (private)

## Status

Accepted

## Context and Problem Statement

The `idi-company-info` orchestrator is packaged as a Docker image and deployed
to an EC2 instance via an Auto Scaling Group. We need to decide where to host
the image and whether a single registry is sufficient.

## Decision Drivers

- EC2 instances must be able to pull the image reliably and efficiently from
  within AWS.
- The orchestrator code is open source and transparency of the deployed artifact
  is desirable.
- The pipeline should remain auditable: the exact image running in AWS should be
  traceable to a public artifact.
- Operational complexity should be minimized where trade-offs allow.

## Considered Options

1. **GHCR only** — build and push to GitHub Container Registry; EC2 pulls
   directly from GHCR.
2. **ECR only** — build and push directly to Amazon ECR; no public image.
3. **GHCR as build target, ECR as deploy target (current approach)** — push to
   GHCR on every build, then sync the tagged image to ECR before deployment.

## Decision Outcome

Chosen option: **Option 3 — GHCR as build target, ECR as deploy target**,
because it satisfies both the open-source transparency goal and the AWS
operational requirements without meaningful additional risk.

### Positive Consequences

- **Open-source parity**: the public GHCR image is byte-for-byte identical to
  the image running in AWS (same digest, retagged). External contributors and
  auditors can inspect exactly what is deployed.
- **Reliable EC2 pulls**: ECR is co-located with the EC2 instances in AWS.
  Pulls are faster, incur no egress costs, are not subject to Docker Hub-style
  rate limits, and do not depend on external network availability at instance
  launch time.
- **IAM-controlled access**: ECR pull permissions are granted via the EC2
  instance profile, keeping credentials out of user data scripts entirely.
- **Auditability**: the `sync-ecr` job in `deploy.yml` records the GHCR source
  digest and ECR destination tag in CI logs, creating a clear chain of custody.
- **Separation of build and deploy**: GHCR handles CI/CD image lifecycle
  (versioned tags, `latest`, package visibility); ECR handles runtime access
  control independently of GitHub.

### Negative Consequences

- **Pipeline complexity**: an additional `sync-ecr` job is required after every
  successful build and Pulumi deploy. If the sync fails, the ECR image may lag
  behind the GHCR image.
- **Duplicate storage costs**: images are stored in two registries. At typical
  orchestrator image sizes this is negligible, but grows if image count or size
  increases significantly.
- **Two sources of truth to monitor**: image vulnerability scanning must be
  configured in both registries independently if required.

### Confirmation

Compliance with this ADR can be verified through the following:

- **`sync-ecr` job is a required step in `deploy.yml`**: the job must remain
  present and must depend on both `docker` (GHCR build) and `deploy-pulumi`
  (ECR repo existence) before it runs. Removing or bypassing it violates the
  decision.
- **Digest parity check (manual)**: after any deployment, confirm that the image
  digest pushed to ECR matches the GHCR source by comparing
  `docker inspect --format='{{index .RepoDigests 0}}'` output for both tags.
  The `sync-ecr` job logs record the source GHCR tag and destination ECR tag,
  which can be cross-referenced in the GitHub Actions run history.
- **ECR repo provisioned by Pulumi**: `pulumi/infra/ecr.py` must define the ECR
  repository. A `pulumi preview` or `pulumi up` that removes the ECR repo
  resource should be treated as a breaking change requiring ADR review.
- **GHCR package visibility**: the `idi-company-info-orchestrator` package on
  GHCR must remain public. A visibility change to private would violate the
  open-source parity goal and should trigger reconsideration of this decision.
- **`packages: read` permission on `sync-ecr`**: the job-level permissions block
  in `deploy.yml` must include `packages: read` to authenticate the GHCR pull.
  This can be reviewed as part of any workflow change PR.

## Pros and Cons of the Options

### Option 1 — GHCR only

- Good: simplest pipeline (no sync step).
- Good: single source of truth for the image.
- Bad: EC2 instances pulling from an external public registry at boot time is a
  reliability risk (network dependency, rate limits, authentication complexity).
- Bad: no IAM-native access control; requires managing GHCR tokens on instances.

### Option 2 — ECR only

- Good: simplest AWS operational story; EC2 pulls via IAM with no external
  dependency.
- Good: no sync step required.
- Bad: the deployed image is not publicly inspectable, reducing transparency for
  an open-source project.
- Bad: external contributors cannot pull the exact production image to reproduce
  issues locally without AWS credentials.

### Option 3 — GHCR + ECR (chosen)

- Good: satisfies both transparency and operational requirements.
- Good: GHCR image is the canonical build artifact; ECR is a deployment mirror.
- Neutral: sync adds one job to the pipeline but is idempotent and low-risk.
- Bad: additional complexity and storage cost compared to a single registry.
