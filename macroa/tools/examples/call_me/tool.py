"""call_me — reference tool that phones the user via Twilio.

This file is the canonical example of how to write a Macroa tool.
Copy this directory to ~/.macroa/tools/call_me/ to install it.

Required environment variables (set in ~/.macroa/tools/call_me/.env):
    TWILIO_ACCOUNT_SID   — from twilio.com/console
    TWILIO_AUTH_TOKEN    — from twilio.com/console
    TWILIO_FROM_NUMBER   — your Twilio phone number  (+1xxxxxxxxxx)
    USER_PHONE_NUMBER    — your personal number to call (+1xxxxxxxxxx)

Install Twilio dependency:
    pip install twilio

Usage (after install):
    macroa run "call me"
    macroa run "call me and say the build has failed"
"""

from __future__ import annotations

import os

from macroa.stdlib.schema import Context, DriverBundle, Intent, ModelTier, SkillResult
from macroa.tools.base import BaseTool, ToolManifest

MANIFEST = ToolManifest(
    name="call_me",
    description=(
        "Calls the user's phone via Twilio. "
        "Use when the user asks to be called, phoned, or reached by voice. "
        "Optional parameter: 'message' — what to say when the call connects."
    ),
    triggers=["call me", "phone me", "ring me", "give me a call", "call my phone"],
    version="1.0.0",
    author="example",
    model_tier=None,   # NANO default — routing only, execute() does real work
    persistent=False,  # one-shot, not a background service
    timeout=30,
)

_REQUIRED_ENV = [
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "USER_PHONE_NUMBER",
]
_DEFAULT_MESSAGE = "This is Macroa. You asked me to call you."


class CallMeTool(BaseTool):

    def setup(self, drivers: DriverBundle) -> None:
        """Warn at startup if Twilio config is missing."""
        missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            import logging
            logging.getLogger(__name__).warning(
                "call_me tool: missing env vars %s — tool will fail until set", missing
            )

    def execute(self, intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
        # Check Twilio is installed
        try:
            from twilio.rest import Client  # type: ignore[import]
        except ImportError:
            return SkillResult(
                output="",
                success=False,
                error="twilio package not installed. Run: pip install twilio",
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
            )

        # Validate config
        config = {k: os.environ.get(k, "") for k in _REQUIRED_ENV}
        missing = [k for k, v in config.items() if not v]
        if missing:
            return SkillResult(
                output="",
                success=False,
                error=(
                    f"Missing Twilio config: {', '.join(missing)}. "
                    "Add them to ~/.macroa/tools/call_me/.env"
                ),
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
            )

        message = intent.parameters.get("message", _DEFAULT_MESSAGE).strip()
        if not message:
            message = _DEFAULT_MESSAGE

        try:
            client = Client(config["TWILIO_ACCOUNT_SID"], config["TWILIO_AUTH_TOKEN"])
            call = client.calls.create(
                to=config["USER_PHONE_NUMBER"],
                from_=config["TWILIO_FROM_NUMBER"],
                twiml=f"<Response><Say>{message}</Say></Response>",
            )
            return SkillResult(
                output=f"Call initiated to {config['USER_PHONE_NUMBER']}. (SID: {call.sid})",
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"call_sid": call.sid, "to": config["USER_PHONE_NUMBER"]},
            )
        except Exception as exc:
            return SkillResult(
                output="",
                success=False,
                error=f"Twilio call failed: {exc}",
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
            )
