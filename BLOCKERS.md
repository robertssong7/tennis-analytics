# TennisIQ — Blockers Log

## BLOCKER-001: Bedrock Authentication Failure
**Phase:** 0 — Environment Setup
**Status:** Open
**Date:** 2026-03-11

### Problem
`BEDROCK_API_KEY` in `.env` does not authenticate with boto3 `bedrock-runtime`.

**Decoded key structure:**
`BedrockAPIKey-he07-at-302524629522:a/FgSZ2cwQxiuSIQQfALVFiv0euCFqj4pBNf7q+D7awj23v1GhOZnyTCJVQ=`

This is not standard AWS IAM format. AWS IAM access key IDs begin with `AKIA` or `ASIA` (20 chars). The decoded key ID `BedrockAPIKey-he07-at-302524629522` does not match.

**Error received:**
`UnrecognizedClientException: The security token included in the request is invalid.`

**Spec call pattern is also incomplete:**
The spec's boto3 client creation only passes `aws_access_key_id` — but boto3 requires both `aws_access_key_id` AND `aws_secret_access_key`. The split-decoded secret was tried but still failed.

### What is needed
One of:
1. Valid AWS IAM credentials: `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` for an IAM user/role with Bedrock permissions in `us-east-2`
2. Or `ANTHROPIC_API_KEY` in `.env` for fallback via standard anthropic SDK

### Workaround implemented
`src/bedrock_client.py` tries Bedrock first, falls back to `ANTHROPIC_API_KEY` if present, raises a clear error if neither works. All agent loop AI calls are stubbed behind this client — everything else is built and ready.

### Resolution steps
1. Create an IAM user at https://console.aws.amazon.com/iam/ with `AmazonBedrockFullAccess` policy
2. Generate access key → set in `.env` as:
   ```
   AWS_ACCESS_KEY_ID=AKIA...
   AWS_SECRET_ACCESS_KEY=...
   AWS_DEFAULT_REGION=us-east-2
   ```
3. Run `python3 src/bedrock_client.py` to verify, then re-run `python3 scripts/test_bedrock.py`

---

*Continuing build — all phases proceed. Bedrock calls will work once valid credentials are added.*
