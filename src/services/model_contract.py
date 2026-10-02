"""AgentCore Platform v1.0"""

# Caller-request contract for the model-lifecycle dossier agent.
#
# One module validates everything a caller can send, so there is exactly one
# answer to "what is accepted?". The request boundary calls into here and
# nothing downstream re-parses raw request data.
#
# Two request channels reach this module:
#   * `input` — a short request line (the model identifier, or a note naming
#     the dossier being requested). It is screened and length-capped, and it is
#     never rendered into the report;
#   * `input_context.model_lifecycle` — the structured model record. This is
#     the data channel, and the only one the dossier is built from.
#
# Why the record travels on the structured channel and not inside `input`:
# the platform rewrites personal-data shapes in the free-text request field at
# every node boundary, and a model record is full of proper nouns — the model
# name, the developing team, the independent validator, the monitoring owner.
# Sent as free text they come back as "[MASKED]" and the dossier reads as an
# FSA submission with its responsible parties redacted, at status success. The
# structured channel is not rewritten, so the record survives intact and every
# field is instead validated here, explicitly.
#
# Rules that hold for every field:
#   * numbers are parsed by a finite + bounded parser. NaN and the infinities
#     survive float() and every comparison against them is False, so an
#     unchecked non-finite value renders into a regulatory document as "nan"
#     with nothing in the log to say so;
#   * strings that end up in the rendered dossier are whitespace-collapsed,
#     control-character-stripped and length-capped, so caller text cannot forge
#     a section heading or a separator rule inside the report;
#   * identifiers and labels are restricted to inert alphabets;
#   * a value that fails any check REFUSES the request, naming the field but
#     never repeating the value;
#   * the record is mandatory. There is no partial dossier: an FSA submission
#     assembled from absent data would be a document that looks complete and
#     is not.

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# ── Bounds ────────────────────────────────────────────────────────────────────
# Defaults. The live values come from config/config.yaml `report:` and are
# passed in as `limits`; these are the floor used when no config is reachable.
DEFAULT_MAX_TEXT_CHARS = 2000
DEFAULT_MAX_CHANGE_HISTORY_ENTRIES = 25
DEFAULT_MAX_PERFORMANCE_METRICS = 20
DEFAULT_LINE_WIDTH = 72

MAX_REQUEST_CHARS = 512
# When the record travels on the structured channel, the request line is transport rather
# than a typed request, so it gets the bound that channel already has -- the adapter's
# input_context limit. Bounded either way; only the budget follows the content.
MAX_TRANSPORT_CHARS = 262_144
MAX_CONTEXT_DEPTH = 6
MAX_SECTION_FIELDS = 32

# Magnitude ceiling for any caller-supplied number. Model metrics, record counts
# and feature counts all sit far below it; the bound exists so an absurd value
# cannot be rendered into a regulatory document as fact.
NUMBER_MIN = -1e12
NUMBER_MAX = 1e12

# ── Inert alphabets ───────────────────────────────────────────────────────────
# Identifiers and labels are rendered into the dossier and into audit payloads,
# so they are restricted rather than escaped.
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_LABEL_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_MODEL_TYPE_RE = re.compile(r"^[a-z0-9_+-]{1,32}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Field names are caller-controlled too. One is repeated back in a refusal only
# when it is short, inert, and carries no disallowed pattern of its own.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

_WHITESPACE_RE = re.compile(r"\s+")
# Zero-width and bidi controls: invisible in a rendered document, so they can
# hide a directive from a human reviewer while a model still reads it.
_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
# Markup strip, used only to re-read a string for the screen below — never to
# rewrite a value. A directive split by tags ("ig<b>nore ...") reads as prose
# until the tags come out.
_MARKUP_RE = re.compile(r"<[^<>]{0,64}>")

# ── Personal-data shapes ──────────────────────────────────────────────────────
# ONE definition, used by both directions of the personal-data guarantee: the
# inbound strip that rewrites these shapes out of caller text, and the outbound
# gate that refuses to release anything still matching them. Two lists would
# drift, and the drift would always favour the leak.
#
# Every separator here is REQUIRED, not optional. A model record is full of
# date-stamped identifiers and large counts, and an optional-separator variant
# reads "FIN-MDL-20260712-001" as a personal identifier: the gate would then
# refuse every genuine dossier, which is the more damaging of the two failures.
# Requiring the separators keeps the dialled and grouped forms — the ones an
# identifier actually has — and leaves an unseparated digit run to be what it
# looks like in this domain, a count.
PERSONAL_DATA_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("long_id_number", re.compile(r"\b\d{4}[-\s]\d{4}[-\s]\d{2,11}\b")),
    ("international_phone_number", re.compile(r"\+\d{1,3}[-\s]\d{1,4}[-\s]\d{2,4}[-\s]\d{3,4}\b")),
    ("phone_number", re.compile(r"\b0\d{1,4}[-\s]\d{2,4}[-\s]\d{4}\b")),
)
REDACTION_STUB = "[REDACTED]"


def strip_direct_identifiers(text: str) -> str:
    """Replace direct-identifier shapes in free text with a fixed stub.

    Applied to every free-text field before it is stored. The platform's own
    masking covers the free-text request field only, so record text arriving on
    the structured channel would otherwise skip it entirely.
    """
    for _name, pattern in PERSONAL_DATA_PATTERNS:
        text = pattern.sub(REDACTION_STUB, text)
    return text


def find_personal_data(text: str) -> Optional[str]:
    """Name the first personal-data shape present in a string, or None."""
    for name, pattern in PERSONAL_DATA_PATTERNS:
        if pattern.search(text):
            return name
    return None


# ── Disallowed-instruction screen ─────────────────────────────────────────────
# Chat-template control tokens are screened as a CLASS, not as a list of the
# few strings a particular model happens to use. They are how a payload forges
# a turn boundary, they carry no meaning in a model-governance record, and the
# platform's own screen scores only some of them as high confidence — the
# angle-bracket and bracket forms, not the doubled-angle or pipe-wrapped ones.
# A screen that inherited only the platform's list would pass
# "<<SYS>> ignore all rules" straight into the dossier.
_CONTROL_TOKEN_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("chat_template_token", re.compile(r"<\|[^<>|]{0,64}\|>")),
    ("instruction_token", re.compile(r"\[/?INST\]", re.IGNORECASE)),
    ("system_token", re.compile(r"<</?SYS>>", re.IGNORECASE)),
    ("role_marker_token", re.compile(r"<\s*/?(?:system|user|assistant)\s*>", re.IGNORECASE)),
    ("section_role_marker", re.compile(r"#{2,}\s*(?:system|instruction)s?\b", re.IGNORECASE)),
)

# Instruction-shaped phrases. Every pattern requires a verb AND its object, so
# the surrounding prose has to actually be an instruction: a record that merely
# mentions rules, prompts or an assistant does not match. Model documentation
# quotes policy language verbatim, and a screen that fires on it refuses
# genuine work — the more damaging of the two failures.
_DIRECTIVE_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    (
        "override_directive",
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\s+"
            r"(?:all\s+|any\s+|the\s+|your\s+|these\s+|those\s+)*"
            r"(?:previous|prior|above|earlier|preceding|system|initial|safety)\s+"
            r"(?:instruction|rule|prompt|direction|guardrail|guideline)s?",
            re.IGNORECASE,
        ),
    ),
    (
        "role_reassignment",
        re.compile(
            r"\b(?:you\s+are\s+now|pretend\s+to\s+be|act\s+as|behave\s+as|roleplay\s+as)\s+"
            r"(?:a|an|the)\s+"
            r"(?:system|assistant|language\s+model|ai\s+model|unrestricted|jailbroken|"
            r"admin(?:istrator)?|developer\s+mode)",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt_disclosure",
        re.compile(
            r"\b(?:reveal|print|repeat|show|output|display|disclose)\s+(?:me\s+)?"
            r"(?:your|the)\s+(?:system|initial|original|hidden|full|exact)\s+"
            r"(?:prompt|instruction|rule)s?",
            re.IGNORECASE,
        ),
    ),
)

_ALL_SCREEN_PATTERNS = _CONTROL_TOKEN_PATTERNS + _DIRECTIVE_PATTERNS


class CallerDataError(ValueError):
    """A caller field failed its contract. Carries a field reference, never a value."""


def screen_text(text: str) -> Optional[str]:
    """Name the first disallowed pattern in one string, or None.

    The string is read four ways, and none of the four subsumes the others:
    as received (control tokens have to be seen before any rewrite could
    consume them), with invisible controls removed, with identifier shapes
    collapsed, and with markup removed — a directive split by tags is prose
    until the tags come out and the words re-assemble.
    """
    candidates = (
        text,
        _INVISIBLE_RE.sub("", text),
        strip_direct_identifiers(text),
        _MARKUP_RE.sub("", _INVISIBLE_RE.sub("", text)),
    )
    for candidate in candidates:
        for name, pattern in _ALL_SCREEN_PATTERNS:
            if pattern.search(candidate):
                return name
    return None


def _reference(parent: str, name: object, index: int) -> str:
    """Render a caller-supplied field name safe to repeat in a refusal."""
    if isinstance(name, str) and _SAFE_NAME_RE.match(name) and screen_text(name) is None:
        return f"{parent}.{name}"
    return f"{parent}[field #{index}]"


def screen_payload(value: object, reference: str = "input_context", depth: int = 0) -> Optional[Tuple[str, str]]:
    """Depth-first screen of a parsed payload; returns (pattern, field) or None.

    Mapping KEYS are screened as well as values: a payload delivered as JSON
    can write any pattern into a key, and \\u escapes make a scan of the raw
    request text unreliable — only a scan after parsing sees what the reader
    will see. Nesting is bounded so a pathologically nested payload cannot
    exhaust the stack before the per-field checks run.
    """
    if depth > MAX_CONTEXT_DEPTH:
        return ("nesting_depth", reference)
    if isinstance(value, str):
        hit = screen_text(value)
        return (hit, reference) if hit else None
    if isinstance(value, Mapping):
        for index, (key, item) in enumerate(value.items(), start=1):
            child = _reference(reference, key, index)
            if isinstance(key, str):
                hit = screen_text(key)
                if hit:
                    return (hit, child)
            found = screen_payload(item, child, depth + 1)
            if found:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value, start=1):
            found = screen_payload(item, f"{reference}[{index}]", depth + 1)
            if found:
                return found
        return None
    return None


# ── Field parsers (every failure refuses the request) ─────────────────────────
def parse_number(
    value: object,
    *,
    field: str,
    minimum: float = NUMBER_MIN,
    maximum: float = NUMBER_MAX,
    integer: bool = False,
) -> float:
    """Parse a caller number, or refuse.

    Rejects booleans (True is an int in Python), non-numeric text, NaN and the
    infinities, and anything outside the stated range. A non-finite value that
    reaches a comparison never raises — it simply makes every comparison False.
    In a dossier that goes to a regulator it renders as "nan" beside a metric
    name, indistinguishable at a glance from a measured result.
    """
    if isinstance(value, bool) or value is None:
        raise CallerDataError(f"{field} must be a number")
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except (TypeError, ValueError):
            raise CallerDataError(f"{field} must be a number") from None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        raise CallerDataError(f"{field} must be a number")
    if not math.isfinite(number):
        raise CallerDataError(f"{field} must be a finite number")
    if integer and number != int(number):
        raise CallerDataError(f"{field} must be a whole number")
    if not minimum <= number <= maximum:
        raise CallerDataError(f"{field} must be between {minimum} and {maximum}")
    return float(int(number)) if integer else number


def parse_label(value: object, *, field: str) -> str:
    """Parse an inert label (business function, risk tier, materiality), or refuse."""
    if not isinstance(value, str):
        raise CallerDataError(f"{field} must be text")
    label = value.strip().lower()
    if not _LABEL_RE.match(label):
        raise CallerDataError(f"{field} must be 1-32 characters of lowercase letters, digits or underscores")
    return label


def parse_model_id(value: object, *, field: str) -> str:
    """Parse the model identifier, or refuse.

    Rendered in the dossier header and carried in every audit event, so it is
    restricted to an inert alphabet rather than escaped at each use.
    """
    if not isinstance(value, str):
        raise CallerDataError(f"{field} must be text")
    model_id = value.strip()
    if not _MODEL_ID_RE.match(model_id):
        raise CallerDataError(f"{field} must be 1-64 characters of letters, digits, dots, underscores or hyphens")
    return model_id


def parse_render_text(
    value: object,
    *,
    field: str,
    limit: int,
    required: bool = False,
) -> str:
    """Parse free text that will be rendered into the dossier, or refuse.

    Whitespace is collapsed to single spaces. That is the whole defence against
    caller text forging document structure: the dossier is a line-oriented
    plain-text document whose sections are introduced by a numbered heading on
    its own line above a rule of dashes, so text that cannot contain a newline
    cannot open a section that is not there.
    """
    if value is None and not required:
        return ""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        raise CallerDataError(f"{field} must be text")
    text = _WHITESPACE_RE.sub(" ", _INVISIBLE_RE.sub("", value)).strip()
    if not text:
        if required:
            raise CallerDataError(f"{field} must not be empty")
        return ""
    if len(text) > limit:
        raise CallerDataError(f"{field} must be at most {limit} characters")
    return strip_direct_identifiers(text)


def _parse_date(value: object, *, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CallerDataError(f"{field} must be text")
    date = value.strip()
    if not date:
        return ""
    if not _DATE_RE.match(date):
        raise CallerDataError(f"{field} must be a YYYY-MM-DD date")
    return date


def _parse_flag(value: object, *, field: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise CallerDataError(f"{field} must be true or false")


def _as_mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CallerDataError(f"{field} must be an object")
    if len(value) > MAX_SECTION_FIELDS:
        raise CallerDataError(f"{field} accepts at most {MAX_SECTION_FIELDS} fields")
    return value


# ── Limits ────────────────────────────────────────────────────────────────────
def resolve_limits(declared: object) -> Dict[str, int]:
    """Resolve the declared report bounds into a complete, validated set.

    `declared` is the `report:` block of config/config.yaml as it reaches this
    layer. A malformed or absent block falls back to the module defaults rather
    than to "no bound" — an unbounded document is the failure this exists to
    prevent, so the fallback direction matters.

    The three max_* bounds are read at the request boundary and decide what is
    accepted; line_width is read by the renderer and decides how the document is
    laid out. They are separate readers on purpose: neither can stand in for the
    other, so a test of one cannot pass because the other happened to hold.
    """
    block = declared if isinstance(declared, Mapping) else {}
    resolved: Dict[str, int] = {}
    for key, default, minimum, maximum in (
        ("max_text_chars", DEFAULT_MAX_TEXT_CHARS, 1, 100_000),
        ("max_change_history_entries", DEFAULT_MAX_CHANGE_HISTORY_ENTRIES, 1, 1_000),
        ("max_performance_metrics", DEFAULT_MAX_PERFORMANCE_METRICS, 1, 200),
        ("line_width", DEFAULT_LINE_WIDTH, 40, 200),
    ):
        raw = block.get(key)
        if raw is None or isinstance(raw, bool) or not isinstance(raw, int):
            resolved[key] = default
            continue
        resolved[key] = default if not (minimum <= raw <= maximum) else raw
    return resolved


# ── The whole contract ────────────────────────────────────────────────────────
RECORD_KEY = "model_lifecycle"

_REQUIRED_FIELDS: Sequence[str] = ("model_id", "model_name", "business_function")

# Known model-type aliases. The caller sends an inert key; the dossier renders
# the canonical display name, so an unrecognised key cannot introduce arbitrary
# text into the document header.
_MODEL_TYPE_ALIASES: Dict[str, str] = {
    "gbm": "Gradient Boosting",
    "gradient_boosting": "Gradient Boosting",
    "xgboost": "Gradient Boosting (XGBoost)",
    "lightgbm": "Gradient Boosting (LightGBM)",
    "nn": "Neural Network",
    "neural_network": "Neural Network",
    "dnn": "Deep Neural Network",
    "logistic_regression": "Logistic Regression",
    "logreg": "Logistic Regression",
    "linear_regression": "Linear Regression",
    "random_forest": "Random Forest",
    "rf": "Random Forest",
    "llm": "Large Language Model",
}

_RISK_TIERS = ("critical", "high", "medium", "low")

# Business functions the model-risk framework treats as material by default.
HIGH_RISK_FUNCTIONS = frozenset(
    {
        "credit_scoring",
        "capital_adequacy",
        "market_risk",
        "aml",
        "fraud_detection",
        "underwriting",
    }
)


def normalise_model_type(value: object, *, field: str) -> str:
    """Map an inert model-type key to its canonical display name, or refuse."""
    if value is None:
        return "Unspecified"
    if not isinstance(value, str):
        raise CallerDataError(f"{field} must be text")
    key = value.strip().lower()
    if not key:
        return "Unspecified"
    if not _MODEL_TYPE_RE.match(key):
        raise CallerDataError(
            f"{field} must be 1-32 characters of lowercase letters, digits, underscores, plus or hyphen"
        )
    return _MODEL_TYPE_ALIASES.get(key, key.replace("_", " ").title())


def _parse_development(block: Mapping[str, Any], limits: Dict[str, int]) -> Dict[str, Any]:
    text_cap = limits["max_text_chars"]
    return {
        "rationale": parse_render_text(
            block.get("rationale"), field=f"{RECORD_KEY}.development.rationale", limit=text_cap
        ),
        "developed_by": parse_render_text(
            block.get("developed_by"), field=f"{RECORD_KEY}.development.developed_by", limit=200
        ),
        "development_date": _parse_date(
            block.get("development_date"), field=f"{RECORD_KEY}.development.development_date"
        ),
    }


def _parse_training_data(block: Mapping[str, Any], limits: Dict[str, int]) -> Dict[str, Any]:
    text_cap = limits["max_text_chars"]
    record_count = block.get("record_count")
    features = block.get("features")
    return {
        "description": parse_render_text(
            block.get("description"), field=f"{RECORD_KEY}.training_data.description", limit=text_cap
        ),
        "period": parse_render_text(block.get("period"), field=f"{RECORD_KEY}.training_data.period", limit=100),
        "record_count": (
            int(
                parse_number(
                    record_count,
                    field=f"{RECORD_KEY}.training_data.record_count",
                    minimum=0,
                    maximum=NUMBER_MAX,
                    integer=True,
                )
            )
            if record_count is not None
            else None
        ),
        "features": (
            int(
                parse_number(
                    features,
                    field=f"{RECORD_KEY}.training_data.features",
                    minimum=0,
                    maximum=1_000_000,
                    integer=True,
                )
            )
            if features is not None
            else None
        ),
        "source": parse_render_text(block.get("source"), field=f"{RECORD_KEY}.training_data.source", limit=200),
    }


def _parse_validation(block: Mapping[str, Any], limits: Dict[str, int]) -> Dict[str, Any]:
    text_cap = limits["max_text_chars"]
    return {
        "methodology": parse_render_text(
            block.get("methodology"), field=f"{RECORD_KEY}.validation.methodology", limit=text_cap
        ),
        "validated_by": parse_render_text(
            block.get("validated_by"), field=f"{RECORD_KEY}.validation.validated_by", limit=200
        ),
        "validation_date": _parse_date(block.get("validation_date"), field=f"{RECORD_KEY}.validation.validation_date"),
        "independent": _parse_flag(block.get("independent"), field=f"{RECORD_KEY}.validation.independent"),
    }


def _parse_performance_metrics(value: object, limits: Dict[str, int]) -> List[Dict[str, Any]]:
    """Parse the metric table into an ordered list of (label, finite number)."""
    field = f"{RECORD_KEY}.performance_metrics"
    block = _as_mapping(value, field=field)
    if len(block) > limits["max_performance_metrics"]:
        raise CallerDataError(f"{field} accepts at most {limits['max_performance_metrics']} metrics")
    metrics: List[Dict[str, Any]] = []
    for index, (name, raw) in enumerate(block.items(), start=1):
        label = parse_label(name, field=f"{field}[metric #{index}] name")
        metrics.append(
            {
                "name": label,
                "value": parse_number(raw, field=f"{field}.{label}"),
            }
        )
    return metrics


def _parse_risk_parameters(value: object, limits: Dict[str, int]) -> Dict[str, Any]:
    field = f"{RECORD_KEY}.risk_parameters"
    block = _as_mapping(value, field=field)
    risk_tier = block.get("risk_tier")
    materiality = block.get("materiality")
    tier = parse_label(risk_tier, field=f"{field}.risk_tier") if risk_tier is not None else "unknown"
    if tier != "unknown" and tier not in _RISK_TIERS:
        raise CallerDataError(f"{field}.risk_tier must be one of {', '.join(_RISK_TIERS)}")
    return {
        "risk_tier": tier,
        "materiality": (
            parse_label(materiality, field=f"{field}.materiality") if materiality is not None else "unknown"
        ),
        "pd_range": parse_render_text(block.get("pd_range"), field=f"{field}.pd_range", limit=200),
        "risk_appetite": parse_render_text(
            block.get("risk_appetite"), field=f"{field}.risk_appetite", limit=limits["max_text_chars"]
        ),
    }


def _parse_change_history(value: object, limits: Dict[str, int]) -> List[Dict[str, str]]:
    field = f"{RECORD_KEY}.change_history"
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise CallerDataError(f"{field} must be a list of change entries")
    cap = limits["max_change_history_entries"]
    if len(value) > cap:
        raise CallerDataError(f"{field} accepts at most {cap} entries")
    entries: List[Dict[str, str]] = []
    for index, entry in enumerate(value, start=1):
        ref = f"{field}[{index}]"
        if not isinstance(entry, Mapping):
            raise CallerDataError(f"{ref} must be an object")
        version = entry.get("version")
        if version is None:
            version_text = f"v{index}"
        elif isinstance(version, str) and _VERSION_RE.match(version.strip()):
            version_text = version.strip()
        else:
            raise CallerDataError(
                f"{ref}.version must be 1-32 characters of letters, digits, dots, underscores or hyphens"
            )
        entries.append(
            {
                "version": version_text,
                "date": _parse_date(entry.get("date"), field=f"{ref}.date"),
                "change": parse_render_text(entry.get("change"), field=f"{ref}.change", limit=limits["max_text_chars"]),
            }
        )
    return entries


def _parse_monitoring(value: object, limits: Dict[str, int]) -> Dict[str, str]:
    field = f"{RECORD_KEY}.monitoring"
    block = _as_mapping(value, field=field)
    return {
        "schedule": parse_render_text(block.get("schedule"), field=f"{field}.schedule", limit=100),
        "next_review": _parse_date(block.get("next_review"), field=f"{field}.next_review"),
        "owner": parse_render_text(block.get("owner"), field=f"{field}.owner", limit=200),
        "threshold": parse_render_text(
            block.get("threshold"), field=f"{field}.threshold", limit=limits["max_text_chars"]
        ),
    }


def build_model_contract(
    request_line: object,
    input_context: object,
    limits: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Screen, validate and normalise everything the caller sent.

    Returns the validated model record. Raises CallerDataError naming the
    offending field — and only the field — when anything fails.
    """
    resolved = resolve_limits(limits)
    context: Mapping[str, Any] = input_context if isinstance(input_context, Mapping) else {}

    found = screen_payload(context, "input_context")
    if found:
        raise CallerDataError(f"{found[1]} contains a disallowed instruction pattern")

    line = request_line.strip() if isinstance(request_line, str) else ""
    if not line:
        raise CallerDataError("input must not be empty")
    # MAX_REQUEST_CHARS bounds a TYPED REQUEST -- a sentence naming what the caller wants. When
    # the record is already on the structured channel, the request line is transport: on
    # Marketplace the caller pastes the record as their chat message, the adapter hook lifts it
    # into input_context (where the S-2 gate does not mask it), and the same text also arrives
    # here as user_input. Measuring a pasted record against a sentence's budget refuses every
    # such request. The record itself is bounded by its own field, depth and magnitude checks
    # below, so nothing is left unbounded. The screen still runs on the line either way.
    line_limit = MAX_REQUEST_CHARS if context.get(RECORD_KEY) is None else MAX_TRANSPORT_CHARS
    if len(line) > line_limit:
        raise CallerDataError(f"input must be at most {line_limit} characters")
    if screen_text(line) is not None:
        raise CallerDataError("input contains a disallowed instruction pattern")

    raw_record = context.get(RECORD_KEY)
    if raw_record is None:
        raise CallerDataError(
            f"input_context.{RECORD_KEY} is required — send the model record on the "
            "structured channel, not inside the request line"
        )
    record = _as_mapping(raw_record, field=f"input_context.{RECORD_KEY}")

    missing = [name for name in _REQUIRED_FIELDS if record.get(name) in (None, "")]
    if missing:
        raise CallerDataError(f"input_context.{RECORD_KEY} is missing required fields: {', '.join(sorted(missing))}")

    model_id = parse_model_id(record.get("model_id"), field=f"{RECORD_KEY}.model_id")
    model_name = parse_render_text(
        record.get("model_name"),
        field=f"{RECORD_KEY}.model_name",
        limit=200,
        required=True,
    )
    business_function = parse_label(record.get("business_function"), field=f"{RECORD_KEY}.business_function")

    risk_parameters = _parse_risk_parameters(record.get("risk_parameters"), resolved)

    return {
        "model_id": model_id,
        "model_name": model_name,
        "model_type": normalise_model_type(record.get("model_type"), field=f"{RECORD_KEY}.model_type"),
        "business_function": business_function,
        "high_risk_function": business_function in HIGH_RISK_FUNCTIONS,
        "development": _parse_development(
            _as_mapping(record.get("development"), field=f"{RECORD_KEY}.development"), resolved
        ),
        "training_data": _parse_training_data(
            _as_mapping(record.get("training_data"), field=f"{RECORD_KEY}.training_data"), resolved
        ),
        "validation": _parse_validation(
            _as_mapping(record.get("validation"), field=f"{RECORD_KEY}.validation"), resolved
        ),
        "performance_metrics": _parse_performance_metrics(record.get("performance_metrics"), resolved),
        "risk_parameters": risk_parameters,
        "risk_tier": risk_parameters["risk_tier"],
        "materiality": risk_parameters["materiality"],
        "change_history": _parse_change_history(record.get("change_history"), resolved),
        "monitoring": _parse_monitoring(record.get("monitoring"), resolved),
        "limits": resolved,
    }
