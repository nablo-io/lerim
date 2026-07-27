"""Visible-source and evidence helpers over trajectory-v1 trace records.

Every trace cache file is newline-delimited trajectory-v1: record 0 is
``{"role":"meta",...}`` and the rest are ``user`` / ``reasoning`` /
``assistant`` / ``tool`` records. ``user``, ``reasoning``, and plain
``assistant`` records carry visible source-domain text. An ``assistant``
record with ``tool_calls`` has ``content: null`` and is a generated action,
and a ``tool`` record is that action's result, linked by ``tool_call_id``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lerim.adapters.common import is_failed_tool_result_text

EXTERNAL_REPORT_REF_RE = re.compile(
    r"\b(?P<label>PR|pull request|issue)\s*#?\s*(?P<number>\d+)\b",
    re.IGNORECASE,
)
GITHUB_REPORT_URL_RE = re.compile(
    r"github\.com/[^/\s]+/[^/\s]+/(?P<label>pull|pulls|issues)/(?P<number>\d+)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
REDACTION_ONLY_RE = re.compile(r"^(?:\[REDACTED:[A-Za-z_]+\]\s*)+$")

VISIBLE_SOURCE_ROLES = frozenset({"user", "reasoning", "assistant"})


def trajectory_record(raw_line: str) -> dict[str, Any] | None:
    """Parse one trace line into a trajectory-v1 record object."""
    try:
        record = json.loads(raw_line)
    except (TypeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def record_role(record: dict[str, Any]) -> str:
    """Return the lowercase trajectory-v1 role of one record."""
    return str(record.get("role") or "").lower()


def tool_call_names(raw_line: str) -> list[str]:
    """Return the tool names invoked by one assistant tool-call record."""
    record = trajectory_record(raw_line)
    if record is None or record_role(record) != "assistant":
        return []
    calls = record.get("tool_calls")
    if not isinstance(calls, list):
        return []
    return [
        str(call.get("name") or "")
        for call in calls
        if isinstance(call, dict) and call.get("name")
    ]


def tool_result_content(raw_line: str) -> str | None:
    """Return the result text of one tool record, or None for other records."""
    record = trajectory_record(raw_line)
    if record is None or record_role(record) != "tool":
        return None
    content = record.get("content")
    return content if isinstance(content, str) else None


def is_failed_tool_result(raw_line: str) -> bool:
    """Return whether one trace line is a tool record reporting a failed call.

    The rule itself lives in :func:`~lerim.adapters.common.is_failed_tool_result_text`
    so that session indexing, which counts failures while normalizing, and this
    pipeline, which cites them, agree on what a failed call is.
    """
    content = tool_result_content(raw_line)
    return content is not None and is_failed_tool_result_text(content)


def visible_source_message(
    raw_line: str,
    *,
    role: str | None = None,
) -> tuple[str, str] | None:
    """Return the visible conversational role and text for one trajectory-v1 line."""
    record = trajectory_record(raw_line)
    if record is None:
        return None
    line_role = record_role(record)
    if role is not None and line_role != role:
        return None
    if line_role not in VISIBLE_SOURCE_ROLES:
        return None
    if record.get("tool_calls"):
        return None
    content = record.get("content")
    if not isinstance(content, str):
        return None
    text = " ".join(content.split()).strip()
    return (line_role, text) if is_visible_source_text(text) else None


def visible_source_role(raw_line: str) -> str | None:
    """Return the visible conversational role for one trajectory-v1 trace line."""
    message = visible_source_message(raw_line)
    return message[0] if message else None


def visible_source_text(raw_line: str, *, role: str | None = None) -> str | None:
    """Return visible conversational text from one trajectory-v1 trace line."""
    message = visible_source_message(raw_line, role=role)
    return message[1] if message else None


def visible_user_source_lines(trace_path: Path) -> str:
    """Render visible user-authored source lines for strategy extraction."""
    rendered: list[str] = []
    for line_number, raw_line in enumerate(read_trace_lines(trace_path), 1):
        message = visible_source_message(raw_line, role="user")
        if message is None:
            continue
        _, text = message
        if is_continuation_summary_text(text):
            continue
        rendered.append(f"line:{line_number} user: {truncate_source_line(text)}")
    return "\n".join(rendered) or "(none)"


def visible_source_lines(trace_path: Path) -> str:
    """Render visible user, reasoning, and assistant source lines for record repair."""
    rendered: list[str] = []
    for line_number, raw_line in enumerate(read_trace_lines(trace_path), 1):
        message = visible_source_message(raw_line)
        if message is None:
            continue
        role, text = message
        if is_continuation_summary_text(text):
            continue
        rendered.append(f"line:{line_number} {role}: {truncate_source_line(text)}")
    return "\n".join(rendered) or "(none)"


def is_continuation_summary_text(text: str) -> bool:
    """Return whether a user line is an agent-session continuation summary scaffold."""
    normalized = " ".join(text.split()).lower()
    return normalized.startswith("this session is being continued from a previous conversation")


def is_continuation_source_line(raw_line: str) -> bool:
    """Return whether a raw trace line is a continuation-summary scaffold."""
    text = visible_source_text(raw_line)
    return bool(text and is_continuation_summary_text(text))


def truncate_source_line(text: str, limit: int = 6000) -> str:
    """Keep long continued-session summaries bounded without dropping line identity."""
    if len(text) <= limit:
        return text
    edge = max(1, (limit - 20) // 2)
    return f"{text[:edge]} ... {text[-edge:]}"


def is_visible_source_text(value: Any) -> bool:
    """True for visible source-domain text, false for empty or fully redacted text."""
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return False
    return REDACTION_ONLY_RE.match(text) is None


def repair_external_report_refs(record: dict[str, Any], trace_path: Path) -> None:
    """Attach visible source refs for external PR/issue claims when available."""
    claimed_refs = _external_report_refs_in_text(
        " ".join(
            str(record.get(field) or "")
            for field in ("title", "body", "decision", "why", "consequences")
        )
    )
    if not claimed_refs:
        return
    lines = read_trace_lines(trace_path)
    current_refs = [str(ref).strip() for ref in record.get("source_event_refs") or []]
    current_lines = {
        line_number
        for ref in current_refs
        if (line_number := line_ref_number(ref)) is not None
        and 1 <= line_number <= len(lines)
        and not is_continuation_source_line(lines[line_number - 1])
    }
    added: list[str] = []
    for external_ref in claimed_refs:
        if any(
            _line_mentions_external_report(lines[line_number - 1], external_ref)
            for line_number in current_lines
            if 1 <= line_number <= len(lines)
        ):
            continue
        source_line = _find_visible_external_report_line(lines, external_ref)
        if source_line is not None:
            added.append(f"line:{source_line}")
    if added:
        record["source_event_refs"] = [*current_refs, *added]


def supported_external_report_refs(
    record: dict[str, Any],
    trace_path: Path,
) -> list[str]:
    """Return external report refs directly supported by record source refs."""
    claimed_refs = _external_report_refs_in_text(
        " ".join(str(record.get(field) or "") for field in ("title", "body"))
    )
    if not claimed_refs:
        return []
    lines = read_trace_lines(trace_path)
    supported: list[str] = []
    for external_ref in claimed_refs:
        for ref in record.get("source_event_refs") or []:
            line_number = line_ref_number(str(ref))
            if line_number is None or line_number < 1 or line_number > len(lines):
                continue
            if _line_mentions_external_report(lines[line_number - 1], external_ref):
                supported.append(external_ref)
                break
    return supported


def external_report_source_refs(
    record: dict[str, Any],
    trace_path: Path,
    external_refs: list[str],
) -> list[str]:
    """Return source refs that directly mention the supported external reports."""
    lines = read_trace_lines(trace_path)
    refs: list[str] = []
    seen: set[str] = set()
    for external_ref in external_refs:
        source_line = _find_visible_external_report_line(lines, external_ref)
        if source_line is not None:
            normalized_ref = f"line:{source_line}"
            if normalized_ref not in seen:
                refs.append(normalized_ref)
                seen.add(normalized_ref)
            continue
        for ref in record.get("source_event_refs") or []:
            normalized_ref = str(ref).strip()
            line_number = line_ref_number(normalized_ref)
            if line_number is None or line_number < 1 or line_number > len(lines):
                continue
            if _line_mentions_external_report(lines[line_number - 1], external_ref):
                if normalized_ref not in seen:
                    refs.append(normalized_ref)
                    seen.add(normalized_ref)
                break
    return refs


def repair_source_refs_from_evidence_quotes(
    record: dict[str, Any],
    trace_path: Path,
    *,
    role: str | None = None,
) -> None:
    """Attach source refs whose visible text contains model-supplied evidence quotes."""
    quote_refs = _source_refs_for_evidence_quotes(record, trace_path, role=role)
    if not quote_refs:
        return
    existing = [str(ref).strip() for ref in record.get("source_event_refs") or []]
    seen: set[str] = set()
    merged: list[str] = []
    for ref in [*existing, *quote_refs]:
        if ref and ref not in seen:
            merged.append(ref)
            seen.add(ref)
    record["source_event_refs"] = merged


def _source_refs_for_evidence_quotes(
    record: dict[str, Any],
    trace_path: Path,
    *,
    role: str | None = None,
) -> list[str]:
    """Find visible source lines that contain the record's evidence quotes."""
    evidence_refs = [
        " ".join(str(item or "").split()).strip()
        for item in record.get("evidence_refs") or []
        if " ".join(str(item or "").split()).strip()
    ]
    if not evidence_refs:
        return []
    lines = read_trace_lines(trace_path)
    refs: list[str] = []
    seen: set[str] = set()
    for quote in evidence_refs:
        quote_key = quote.lower()
        if len(quote_key) < 12:
            continue
        for line_number, raw_line in enumerate(lines, 1):
            text = visible_source_text(raw_line, role=role)
            if not text:
                continue
            if quote_key not in text.lower():
                continue
            ref = f"line:{line_number}"
            if ref not in seen:
                refs.append(ref)
                seen.add(ref)
            break
    return refs



def record_source_excerpts(
    record: dict[str, Any],
    trace_path: Path,
    *,
    max_excerpts: int = 2,
) -> list[str]:
    """Return short exact source excerpts that overlap with the record claim."""
    terms = _record_terms(record)
    if not terms:
        return []
    lines = read_trace_lines(trace_path)
    candidates: list[tuple[int, int, str]] = []
    for ref in record.get("source_event_refs") or []:
        line_number = line_ref_number(str(ref))
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        text = visible_source_text(lines[line_number - 1])
        if not text:
            continue
        for index, chunk in enumerate(_source_excerpt_chunks(text)):
            score = len(terms & normalized_terms(chunk))
            if score >= 2:
                candidates.append((-score, index, chunk))
    excerpts: list[str] = []
    seen: set[str] = set()
    for _, _, chunk in sorted(candidates):
        excerpt = _compact_source_excerpt(chunk)
        if not excerpt or excerpt in seen:
            continue
        excerpts.append(excerpt)
        seen.add(excerpt)
        if len(excerpts) >= max_excerpts:
            break
    return excerpts


def _record_terms(record: dict[str, Any]) -> set[str]:
    """Return normalized claim terms from a record draft."""
    return normalized_terms(
        " ".join(
            str(record.get(field) or "")
            for field in ("title", "body", "decision", "why")
        )
    )


def normalized_terms(text: str) -> set[str]:
    """Tokenize text into simple lowercase terms for source-excerpt selection."""
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}", text.lower()):
        token = token.strip("_+-")
        if len(token) < 4:
            continue
        if token.endswith("s") and len(token) > 4:
            token = token[:-1]
        terms.add(token)
    return terms


def _source_excerpt_chunks(text: str) -> list[str]:
    """Split visible source text into short candidate evidence chunks."""
    chunks: list[str] = []
    for raw_chunk in re.split(r"[\n\r]+", text):
        chunk = " ".join(raw_chunk.split()).strip(" -*")
        if len(chunk) >= 12:
            chunks.append(chunk)
    return chunks


def _compact_source_excerpt(chunk: str, limit: int = 220) -> str:
    """Keep an exact but concise prefix of a source chunk."""
    excerpt = " ".join(chunk.split()).strip()
    for separator in (" — ", " - "):
        if separator in excerpt:
            prefix = excerpt.split(separator, 1)[0].strip()
            if len(prefix) >= 12:
                excerpt = prefix
                break
    if len(excerpt) <= limit:
        return excerpt
    return excerpt[:limit].rsplit(" ", 1)[0].strip()


def evidence_ref_supported_by_visible_source(
    evidence_ref: str,
    lines: list[str],
) -> bool:
    """Return whether an evidence string appears in visible source text."""
    normalized = " ".join(evidence_ref.split()).strip().lower()
    if len(normalized) < 4:
        return False
    external_refs = _external_report_refs_in_text(evidence_ref)
    for raw_line in lines:
        text = visible_source_text(raw_line)
        if not text:
            continue
        line_text = " ".join(text.split()).lower()
        if normalized in line_text:
            return True
        if external_refs and any(
            _line_mentions_external_report(raw_line, external_ref)
            for external_ref in external_refs
        ):
            return True
    return False


def line_contains_visible_quote(
    lines: list[str],
    line_number: int,
    quote: str,
) -> bool:
    """Return whether a source line visibly contains a model-supplied quote."""
    if line_number < 1 or line_number > len(lines):
        return False
    text = visible_source_text(lines[line_number - 1])
    if not text:
        return False
    return " ".join(quote.split()).lower() in " ".join(text.split()).lower()


def source_ref_for_visible_quote(lines: list[str], quote: str) -> str | None:
    """Find the visible source line containing a quote."""
    normalized_quote = " ".join(quote.split()).strip().lower()
    if len(normalized_quote) < 12:
        return None
    for line_number, raw_line in enumerate(lines, 1):
        text = visible_source_text(raw_line)
        if not text:
            continue
        if normalized_quote in " ".join(text.split()).lower():
            return f"line:{line_number}"
    return None


def _external_report_refs_in_text(text: str) -> list[str]:
    """Extract normalized external PR/issue references from text."""
    refs: list[str] = []
    seen: set[str] = set()
    for match in GITHUB_REPORT_URL_RE.finditer(text):
        label = match.group("label").lower()
        number = match.group("number")
        normalized_label = "PR" if label in {"pull", "pulls"} else "issue"
        ref = f"{normalized_label} #{number}"
        key = ref.lower()
        if key not in seen:
            refs.append(ref)
            seen.add(key)
    for match in EXTERNAL_REPORT_REF_RE.finditer(text):
        label = match.group("label").lower()
        number = match.group("number")
        normalized_label = "PR" if label in {"pr", "pull request"} else "issue"
        ref = f"{normalized_label} #{number}"
        key = ref.lower()
        if key not in seen:
            refs.append(ref)
            seen.add(key)
    return refs


def _find_visible_external_report_line(lines: list[str], external_ref: str) -> int | None:
    """Find a visible source line that states the external PR/issue reference."""
    for index, raw_line in enumerate(lines, 1):
        message = visible_source_message(raw_line)
        if message is None:
            continue
        _, text = message
        if is_continuation_summary_text(text):
            continue
        if _line_mentions_external_report(raw_line, external_ref):
            return index
    return None


def _line_mentions_external_report(raw_line: str, external_ref: str) -> bool:
    """Return whether a raw visible trace line mentions the normalized report ref."""
    text = visible_source_text(raw_line) or ""
    return external_ref.lower() in {
        ref.lower() for ref in _external_report_refs_in_text(text)
    }


def brief_claim(*values: Any) -> str:
    """Build a compact claim from source-backed model fields."""
    sentences: list[str] = []
    seen: set[str] = set()
    for value in values:
        sentence = first_sentence(value)
        key = sentence.lower()
        if sentence and key not in seen:
            sentences.append(sentence)
            seen.add(key)
        if len(sentences) >= 2:
            break
    return " ".join(sentences)


def compact_deferred_title(title: str) -> str:
    """Keep deferred-design titles on the design and deferral, not rationale."""
    normalized = str(title or "").strip().rstrip(".")
    lowered = normalized.lower()
    marker = " deferred"
    if marker in lowered:
        end = lowered.index(marker) + len(marker)
        return normalized[:end].strip()
    return normalized or "Deferred design"


def shortest_visible_source_text(
    record: dict[str, Any],
    trace_path: Path,
    *,
    role: str | None = None,
) -> str:
    """Return the shortest visible cited source text for compact quote-backed bodies."""
    lines = read_trace_lines(trace_path)
    candidates: list[str] = []
    for ref in record.get("source_event_refs") or []:
        line_number = line_ref_number(str(ref))
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        text = visible_source_text(lines[line_number - 1], role=role)
        if text:
            candidates.append(text)
    if not candidates:
        return ""
    return min(candidates, key=len)


def first_sentences(value: Any, *, count: int) -> str:
    """Return the first count simple sentences from text."""
    text = " ".join(str(value or "").split()).strip()
    if not text or count <= 0:
        return ""
    sentences: list[str] = []
    remaining = text
    while remaining and len(sentences) < count:
        split_at: int | None = None
        for delimiter in (". ", "; "):
            index = remaining.find(delimiter)
            if index >= 0 and (split_at is None or index < split_at):
                split_at = index
        if split_at is None:
            sentences.append(remaining.rstrip(".") + ".")
            break
        sentence = remaining[:split_at].strip().rstrip(".")
        if sentence:
            sentences.append(sentence + ".")
        remaining = remaining[split_at + 2 :].strip()
    return " ".join(sentences)


def first_sentence(value: Any) -> str:
    """Return the first sentence from a model-written field."""
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    for delimiter in (". ", "; "):
        if delimiter in text:
            return text.split(delimiter, 1)[0].strip().rstrip(".") + "."
    return text.rstrip(".") + "."


def prune_unusable_source_refs(record: dict[str, Any], trace_path: Path) -> None:
    """Drop or repair refs pointing at meta, tool-call, or tool-result records."""
    refs = [str(ref).strip() for ref in record.get("source_event_refs") or []]
    if not refs:
        return
    lines = read_trace_lines(trace_path)
    kept: list[str] = []
    for ref in refs:
        line_number = line_ref_number(ref)
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        raw_line = lines[line_number - 1].strip()
        if not raw_line:
            continue
        source_kind = _source_line_kind(raw_line)
        if source_kind == "visible":
            kept.append(ref)
            continue
        if source_kind == "tool":
            repaired = _nearby_visible_assistant_source_ref(line_number, lines)
            if repaired is not None:
                kept.append(repaired)
            continue
        repaired = _nearby_visible_source_ref(line_number, lines)
        if repaired is not None:
            kept.append(repaired)
    record["source_event_refs"] = kept


def _source_line_kind(raw_line: str) -> str | None:
    """Classify a trajectory-v1 line as visible text, tool call, hidden, or none."""
    record = trajectory_record(raw_line)
    if record is None:
        return None
    line_role = record_role(record)
    if line_role == "assistant" and record.get("tool_calls"):
        return "tool"
    if line_role == "tool":
        return "hidden"
    if line_role not in VISIBLE_SOURCE_ROLES:
        return None
    content = record.get("content")
    if not isinstance(content, str):
        return None
    return "visible" if is_visible_source_text(content) else "hidden"


def _nearby_visible_source_ref(line_number: int, lines: list[str]) -> str | None:
    """Find nearby visible source-domain text for a model-cited tool-result line."""
    for distance in range(1, 4):
        for candidate in (line_number + distance, line_number - distance):
            if candidate < 1 or candidate > len(lines):
                continue
            if visible_source_role(lines[candidate - 1]) is not None:
                return f"line:{candidate}"
    return None


def _nearby_visible_assistant_source_ref(
    line_number: int,
    lines: list[str],
) -> str | None:
    """Find nearby assistant text that explains a generated tool action."""
    for distance in range(1, 4):
        for candidate in (line_number - distance, line_number + distance):
            if candidate < 1 or candidate > len(lines):
                continue
            if visible_source_text(lines[candidate - 1], role="assistant"):
                return f"line:{candidate}"
    return None


def read_trace_lines(trace_path: Path) -> list[str]:
    """Read trace lines for lightweight source-ref validation."""
    try:
        return trace_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def line_ref_number(ref: str) -> int | None:
    """Parse line refs of the form line:123."""
    prefix = "line:"
    if not ref.startswith(prefix):
        return None
    try:
        return int(ref[len(prefix) :])
    except ValueError:
        return None
