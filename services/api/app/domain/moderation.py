"""Deterministic preflight policy.

This module intentionally performs narrow, explainable checks. It is not a
replacement for a specialized safety vendor or legal review. Every decision is
versioned and stored so later policy changes do not rewrite history.
"""
from __future__ import annotations

import re
from typing import Any

POLICY_VERSION = "policy-v1"

BLOCK_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("unauthorized_voice_clone", re.compile(r"(?:克隆|复制|clone|deepfake)\s*(?:[\w\u4e00-\u9fff· ]{1,40})?(?:声音|声线|嗓音|voice)", re.I)),
    ("identity_impersonation", re.compile(r"(?:冒充|假扮|impersonat(?:e|ion))\s*(?:真人|歌手|artist|person|[\w\u4e00-\u9fff· ]{1,40})", re.I)),
)

REVIEW_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("artist_style_reference", re.compile(r"(?:像|模仿|仿照|in the style of|sound like|like)\s*[\w\u4e00-\u9fff· ]{2,40}", re.I)),
    ("third_party_material_claim", re.compile(r"(?:原曲|翻唱|cover|采样|sample|remix|改编|adaptation)", re.I)),
    ("public_figure_reference", re.compile(r"(?:明星|艺人|歌手|celebrity|public figure)", re.I)),
)


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return "\n".join(_flatten(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return "\n".join(_flatten(v) for v in value)
    return str(value)


def evaluate_policy(subject: Any, policy_version: str = POLICY_VERSION) -> dict[str, Any]:
    text = _flatten(subject)[:100_000]
    reasons: list[dict[str, str]] = []
    for code, pattern in BLOCK_RULES:
        match = pattern.search(text)
        if match:
            reasons.append({"code": code, "severity": "block", "evidence": match.group(0)[:120]})
    if reasons:
        return {"status": "block", "policy_version": policy_version, "reasons": reasons}
    for code, pattern in REVIEW_RULES:
        match = pattern.search(text)
        if match:
            reasons.append({"code": code, "severity": "review", "evidence": match.group(0)[:120]})
    return {"status": "review" if reasons else "allow", "policy_version": policy_version, "reasons": reasons}
