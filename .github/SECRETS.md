# Required GitHub Secrets for CI Workflow

Configure these secrets in your repository: **Settings → Secrets and variables → Actions**.

## Required for all branches

| Secret | Description |
|--------|-------------|
| `GITHUB_TOKEN` | Automatically provided by GitHub. Used for GHCR push and release creation. |
| `GH_TOKEN` | Optional. Use if `GITHUB_TOKEN` lacks write permissions (e.g. restricted org). Must have `contents: write` for commits/tags/releases. |

## Required for Pulumi deploy and ECR sync

| Secret | Description |
|--------|-------------|
| `AWS_ACCESS_KEY_ID` | AWS IAM access key for Pulumi and ECR. |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM secret key. |
| `AWS_REGION` | Optional. AWS region (default: `us-east-2`). |
| `PULUMI_ACCESS_TOKEN` | Pulumi Cloud access token (if using Pulumi Cloud backend). |
| `ECR_REPOSITORY_PREFIX` | Optional. Override ECR repository name prefix (default: `idi-company-information`). |

## ECR setup

Create these ECR repositories in your AWS account:

- `idi-company-information-orchestrator`
- `idi-company-information-scheduler`

Or set `ECR_REPOSITORY_PREFIX` to use a different prefix.

## Code scanning

CodeQL and pip-audit run automatically. For GitHub Advanced Security (CodeQL), ensure it is enabled for the repository. pip-audit fails on any known dependency vulnerability; remove or adjust the step if you need different behavior.
