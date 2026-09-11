# ToolifyHub OCI ARM Capacity Hunter

Automated cloud-native runner that periodically checks Oracle Cloud Infrastructure (OCI) for `VM.Standard.A1.Flex` (2 OCPU / 12 GB RAM) host capacity and provisions the instance immediately upon availability.

## Architecture & Security
- Runs fully in-memory on GitHub-hosted Ubuntu runners.
- Zero credentials or private keys in Git history (100% GitHub Actions Secrets).
- Automated deduplication: checks for existing running instances before attempting launch.
- Concurrency protected to prevent overlapping runs.
