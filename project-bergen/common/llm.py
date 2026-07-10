"""Single LLM gateway for every component.

Two modes, chosen once at import time and stamped onto every output:
  - "api":     ANTHROPIC_API_KEY present -> real calls (haiku for routing,
               sonnet for drafting, per config.yaml).
  - "offline": no key (or BERGEN_OFFLINE=1) -> callers use their
               deterministic rule/template fallbacks. complete() raises so a
               missing fallback is a loud bug, never a silent one.

Workflow-first rule: this module exposes exactly one primitive, complete().
There are no tool loops, no agents, no retries-until-it-looks-right.
"""
import os

from common.util import load_config

_cfg = load_config()


def mode() -> str:
    if os.environ.get("BERGEN_OFFLINE") == "1":
        return "offline"
    return "api" if os.environ.get("ANTHROPIC_API_KEY") else "offline"


def complete(role: str, system: str, prompt: str) -> str:
    """One LLM call. role is 'classifier' | 'drafter' | 'judge' (looked up in
    config.yaml). Raises RuntimeError in offline mode — callers must catch the
    mode *before* calling and use their deterministic fallback instead."""
    if mode() == "offline":
        raise RuntimeError(
            "LLM called in offline mode - caller must use its deterministic fallback")
    import anthropic  # imported lazily so offline mode needs no SDK
    client = anthropic.Anthropic()
    models = _cfg["models"]
    resp = client.messages.create(
        model=models[role],
        max_tokens=models[f"{role}_max_tokens"],
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()
