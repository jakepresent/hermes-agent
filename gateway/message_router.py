"""Conservative first-message model routing for Jake's post-Microsoft Hermes.

This module is deliberately deterministic. It classifies only the first user
message in a new conversation; follow-up turns keep the session's existing
model so prompt caching and conversational continuity remain intact.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
import json
import re
from typing import Iterable


@dataclass(frozen=True)
class Route:
    lane: str
    reason: str


_IMAGE_MARKERS = ("[image attached at:", "[screenshot]", '"type": "image"')
_DOCUMENT_MARKERS = (
    "[the user sent a text document:",
    "[the user sent a document:",
    "[file attached at:",
    "[document attached at:",
)

_CODE_OBJECTS = re.compile(
    r"\b(repo(?:sitory)?|code(?:base)?|source|function|class|method|api|cli|sdk|"
    r"tests|testing|build|compile|branch|commit|pr|pull request|bug|regression|"
    r"python|swift|typescript|javascript|rust|yaml|json|database|migration|"
    r"gateway|service|app|implementation|mcp|wireguard|vpn|network|aside|agent)\b|(?:^|[\s/])[\w.-]+\.(?:py|swift|ts|tsx|js|rs|yaml|yml|json|toml|md)\b",
    re.I,
)
_CODE_ACTIONS = re.compile(
    r"\b(implement|fix|debug|patch|refactor|build|change|edit|modify|update|add|remove|"
    r"write|create|test|correct|improve|migrate|merge|cherry-pick|revert|run tests?|ship|deploy)\b",
    re.I,
)
_CODE_JUDGMENT = re.compile(
    r"\b(review|audit|investigate|diagnose|analy[sz]e|design|plan|architecture|root cause|"
    r"do a pass|make sure|look(?:ing)? at|"
    r"why (?:did|does|is|was)|should (?:we|i))\b",
    re.I,
)
_RESEARCH_INTENT = re.compile(
    r"\b(latest|current(?:ly)?|today|right now|up[- ]to[- ]date|price|pricing|availability|ready|"
    r"available|ready|hours|schedule|weather|news|release|version|model card|benchmark|"
    r"look into|research|find out|compare|set up|configure|which (?:one|model|product|service)|"
    r"which [^?.!]{0,60} should i (?:get|buy)|which (?:one )?should i (?:get|buy)|what should i (?:get|buy)|"
    r"best (?:model|product|service|place|option))\b",
    re.I,
)
_EXTERNAL_OBJECT = re.compile(
    r"\b(model|provider|api|product|service|restaurant|hotel|flight|train|amtrak|trip|travel|"
    r"policy|rules?|law|insurance|plan|price|domain|website|store|hours|schedule|weather|news|"
    r"release|version|device|hardware|gpu|computer|macbook|macos|os|beta|phone|camera|film|developer|chemical|"
    r"cable|card|wallet|airtag|car|test drive|subscription|codex|hermes|authentication)\b",
    re.I,
)
_DECISION_LANGUAGE = re.compile(
    r"\b(should\s+(?:i|we)|how (?:much |far )?(?:can/)?should\s+i|how should\s+(?:i|we)|"
    r"what do you think|thoughts on|help me decid\w*|decid\w*|which should|best options?|recommend|"
    r"(?:what|anything) should i do|anything i should do|prepare (?:me )?for (?:a |my )?(?:call|meeting|interview)|"
    r"interpretation|interpret this|"
    r"evaluate|pressure[- ]test|trade[- ]offs?|risk|safe|worth it|"
    r"consequential|high[- ]stakes|cause(?:d)?|attribut(?:e|ion)|incident)\b",
    re.I,
)
_SENSITIVE_DOMAIN = re.compile(
    r"\b(health|medical|medication|dose|sertraline|hormone|symptom|doctor|nutrition|"
    r"finance|financial|investment|tax|retirement|hsa|espp|stock|shares?|msft|money|legal|lawyer|"
    r"citizenship|immigration|career|job offer|interview|employer|microsoft|relationship|"
    r"dating|partner|conflict|incident|icm|customer impact|security|privacy|credentials?|"
    r"chemical|developer)\b",
    re.I,
)
_EXACT_EVIDENCE = re.compile(
    r"\b(exact(?:ly)?|cite|source(?:s)?|evidence|verify|verified|ground(?:ed|ing)?|"
    r"do not (?:guess|infer|invent)|without (?:guessing|inference)|caus(?:e|al|ality))\b",
    re.I,
)


def _route(lane: str, reason: str) -> Route:
    return Route(lane, reason)


def _active_message_text(text: str) -> str:
    """Remove gateway-provided channel/reply context from routing input."""
    raw = text or ""
    lowered = raw.lower()
    if "[new message]" in lowered:
        return raw[lowered.rfind("[new message]") + len("[new message]"):].strip()
    matches = list(re.finditer(r"\]\s*\[(?:jake|jpresent)\]\s*", raw, re.I))
    if matches:
        tail = raw[matches[-1].end():].strip()
        if tail and "user sent a message with no text content" not in tail.lower():
            return tail
    return raw


def classify_first_message(text: str, *, has_image: bool = False, has_document: bool = False) -> Route:
    """Return a conservative route for the first message of a new session."""
    raw = _active_message_text(text or "")
    lowered = raw.lower()
    has_image = has_image or any(marker in lowered for marker in _IMAGE_MARKERS)
    has_document = has_document or any(marker in lowered for marker in _DOCUMENT_MARKERS)

    if has_document:
        return _route("judgment", "document attachment")
    if has_image:
        return _route("vision_research", "image attachment")

    code = bool(_CODE_OBJECTS.search(raw))
    code_action = bool(_CODE_ACTIONS.search(raw))
    code_judgment = bool(_CODE_JUDGMENT.search(raw))
    sensitive = bool(_SENSITIVE_DOMAIN.search(raw))
    decision = bool(_DECISION_LANGUAGE.search(raw))
    exact = bool(_EXACT_EVIDENCE.search(raw))
    current_research = bool(_RESEARCH_INTENT.search(raw) and _EXTERNAL_OBJECT.search(raw)) or bool(
        re.search(r"https?://", raw, re.I)
    )

    if code and code_judgment and not code_action:
        return _route("judgment", "code or architecture judgment")
    if sensitive:
        return _route("judgment", "consequential domain")
    if decision and exact:
        return _route("judgment", "evidence-sensitive judgment")
    if code and decision:
        return _route("judgment", "consequential code decision")
    if code and code_action:
        return _route("coding", "bounded repository or code action")
    if current_research:
        return _route("vision_research", "current external research")
    if decision:
        return _route("judgment", "explicit decision or recommendation request")
    return _route("routine", "no high-confidence specialist rule")


def classify_records(records: Iterable[dict]) -> list[dict]:
    output = []
    for record in records:
        decision = classify_first_message(
            str(record.get("text") or ""),
            has_image=bool(record.get("has_image")),
            has_document=bool(record.get("has_document")),
        )
        output.append({**record, "route": asdict(decision)})
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="?")
    parser.add_argument("--image", action="store_true")
    parser.add_argument("--document", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asdict(classify_first_message(args.text or "", has_image=args.image, has_document=args.document)), indent=2))


if __name__ == "__main__":
    main()
