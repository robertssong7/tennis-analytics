"""
TennisIQ — Bedrock / Anthropic AI Client
Tries AWS Bedrock first. Falls back to Anthropic SDK if ANTHROPIC_API_KEY is set.
All credentials read from environment — never hardcoded.
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

# Model IDs
HAIKU_MODEL_ID  = "us.anthropic.claude-haiku-4-5-20251001-v1:0"   # overnight loops
SONNET_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20251001-v1:0"  # build tasks

HAIKU_ANTHROPIC  = "claude-haiku-4-5-20251001"
SONNET_ANTHROPIC = "claude-sonnet-4-5-20251001"


def _load_env():
    """Load .env file into os.environ if not already set."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k not in os.environ:
                        os.environ[k] = v


def _get_bedrock_client():
    """Build boto3 bedrock-runtime client using AWS IAM credentials from env."""
    import boto3
    _load_env()

    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key  = os.getenv("AWS_SECRET_ACCESS_KEY")
    region      = os.getenv("AWS_DEFAULT_REGION", "us-east-2")

    if not access_key or not secret_key:
        raise EnvironmentError(
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set in .env. "
            "See BLOCKERS.md for setup instructions."
        )

    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def invoke(prompt: str, model: str = "haiku", max_tokens: int = 1000) -> str:
    """
    Invoke the AI model with a text prompt.
    Tries Bedrock first, falls back to Anthropic SDK.

    Args:
        prompt: User message text
        model: "haiku" (overnight loops) or "sonnet" (build tasks)
        max_tokens: Maximum tokens in response

    Returns:
        Response text string
    """
    _load_env()

    bedrock_model  = HAIKU_MODEL_ID  if model == "haiku" else SONNET_MODEL_ID
    anthropic_model = HAIKU_ANTHROPIC if model == "haiku" else SONNET_ANTHROPIC

    # --- Try Bedrock first ---
    aws_key = os.getenv("AWS_ACCESS_KEY_ID")
    if aws_key:
        try:
            client = _get_bedrock_client()
            response = client.invoke_model(
                modelId=bedrock_model,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                }),
            )
            result = json.loads(response["body"].read())
            return result["content"][0]["text"]
        except Exception as e:
            logger.warning("Bedrock call failed: %s — trying Anthropic fallback", e)

    # --- Fallback to Anthropic SDK ---
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
        msg = client.messages.create(
            model=anthropic_model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    raise EnvironmentError(
        "No AI credentials available. Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY "
        "or ANTHROPIC_API_KEY in .env. See BLOCKERS.md."
    )


if __name__ == "__main__":
    # Quick connectivity test
    print("Testing AI client...")
    try:
        reply = invoke("Say 'Bedrock connected' and nothing else.", model="haiku", max_tokens=20)
        print("Response:", reply)
        print("AI client: OK")
    except EnvironmentError as e:
        print("Config error:", e)
    except Exception as e:
        print("Error:", type(e).__name__, e)
