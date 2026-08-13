"""Intelligent CSV ingestion pipeline (proposal §14–§24).

Encoding → delimiter → header → column analysis → sample-value analysis →
semantic mapping with confidence → canonical normalization. Unknown columns are
never discarded: they are preserved verbatim in `custom_metadata` (§19).
"""

import csv
import io
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import chardet

logger = logging.getLogger(__name__)

VALID_ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}

# Canonical targets offered in the manual mapping dropdown (§18).
CANONICAL_FIELDS = [
    "scene_number",
    "visual_prompt",
    "video_prompt",
    "voiceover_script",
    "master_prompt",
    "negative_prompt",
    "style",
    "tone",
    "voice_name",
    "language",
    "duration",
    "aspect_ratio",
    "media_type",
    "filename",
    "character",
    "camera",
    "lighting",
    "custom_metadata",
    "ignore",
]

# Fields that live inside custom_metadata but are consumed by the prompt engine.
METADATA_FIELDS = {"character", "camera", "lighting"}

# Columns that map onto real `scenes` columns.
SCENE_FIELDS = {
    "scene_number",
    "visual_prompt",
    "video_prompt",
    "voiceover_script",
    "master_prompt",
    "negative_prompt",
    "style",
    "tone",
    "voice_name",
    "language",
    "duration",
    "aspect_ratio",
    "media_type",
    "filename",
}

COLUMN_ALIASES: Dict[str, List[str]] = {
    "scene_number": ["scene_number", "scene", "scene_id", "scene_no", "id", "number", "no", "index", "#", "sl", "serial"],
    "visual_prompt": [
        "visual_prompt", "image_prompt", "visual_description", "image_description",
        "scene_description", "visual", "image", "prompt", "picture_prompt", "art_prompt",
        "concept", "description", "visuals",
    ],
    "video_prompt": ["video_prompt", "video_description", "motion_prompt", "clip_prompt", "video", "animation_prompt"],
    "voiceover_script": [
        "voiceover_script", "voiceover", "voice_over", "narration", "narration_text",
        "narration_script", "voice_script", "audio_script", "script", "speech",
        "speech_text", "dialogue", "voice_text", "audio_text", "text",
    ],
    "master_prompt": ["master_prompt", "master", "global_prompt", "base_prompt", "master_style", "style_prompt"],
    "negative_prompt": ["negative_prompt", "negative", "avoid", "exclude", "do_not_include"],
    "style": ["style", "art_style", "visual_style", "genre", "aesthetic"],
    "tone": ["tone", "voice_tone", "mood", "emotion", "feeling"],
    "voice_name": ["voice_name", "voice", "voice_id", "speaker", "voice_style", "narrator"],
    "language": ["language", "lang", "locale", "language_code"],
    "duration": ["duration", "length", "time", "seconds", "sec", "runtime"],
    "aspect_ratio": ["aspect_ratio", "ratio", "aspect", "image_ratio", "video_ratio", "dimension", "format", "orientation"],
    "media_type": ["media_type", "type", "generation_type", "output_type", "asset_type", "content_type"],
    "filename": ["filename", "file_name", "output_filename", "file", "output", "name", "slug"],
    "character": ["character", "characters", "person", "subject", "actor", "cast"],
    "camera": ["camera", "camera_angle", "camera_movement", "shot", "shot_type", "angle", "framing"],
    "lighting": ["lighting", "light", "lights", "illumination"],
}

# Header tokens that must not be swallowed by a fuzzy match.
AMBIGUITY_GUARD = {"id", "no", "type", "name", "text", "time", "file"}

# Fields whose content is prose. A column of integers or short scalars cannot be
# one of these no matter what its name suggests — "Image Name" holding 1,2,3 is
# a filename column, not an image prompt.
TEXT_FIELDS = {"visual_prompt", "video_prompt", "voiceover_script", "master_prompt", "negative_prompt"}

# Tokens that turn a media word into an identifier rather than a prompt:
# "image name", "video file", "image id" all name an asset, they do not describe one.
IDENTIFIER_TOKENS = {"name", "file", "filename", "id", "number", "no", "index", "ref", "reference", "slug"}
MEDIA_TOKENS = {"image", "visual", "video", "picture", "photo", "clip", "asset", "audio", "voice"}

# Minimum average length before a column is credible as a prompt.
MIN_PROMPT_LENGTH = 25


def _normalize_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (header or "").strip().lower()).strip("_")


def _unique_headers(header_row: List[str]) -> List[str]:
    """Make header names unique and non-empty without discarding any column.

    A CSV with two columns both called `prompt` keeps both, as `prompt` and
    `prompt_2`, so neither column's data is lost (§19).
    """
    headers: List[str] = []
    seen: Dict[str, int] = {}
    for index, raw in enumerate(header_row):
        name = (raw or "").strip().lstrip("﻿") or f"column_{index + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        headers.append(name)
    return headers


def _is_identifier_header(tokens: set) -> bool:
    """True for headers like "Image Name", "Video File", "Clip ID"."""
    return bool(tokens & MEDIA_TOKENS) and bool(tokens & IDENTIFIER_TOKENS)


def _looks_like_prose(values: List[str]) -> bool:
    samples = [v for v in values if v][:50]
    if not samples:
        return False
    return sum(len(v) for v in samples) / len(samples) >= MIN_PROMPT_LENGTH


def classify_column(header: str, values: List[str]) -> Tuple[str, int]:
    """Return (canonical_field, confidence 0-100) from the name and the data (§16, §17)."""
    clean = _normalize_header(header)
    if not clean:
        return "custom_metadata", 0

    tokens = set(clean.split("_"))

    # "Image Name" / "Video File" name an asset rather than describe one, so they
    # must not win a prompt field on the strength of the media word alone.
    if _is_identifier_header(tokens) and not _looks_like_prose(values):
        return "filename", 90

    for field, aliases in COLUMN_ALIASES.items():
        if clean == field:
            return _validate_against_values(field, 99, values)
        if clean in aliases:
            return _validate_against_values(field, 95, values)

    # Token overlap, e.g. "scene_visual_prompt_text" → visual_prompt
    best_field, best_score = "custom_metadata", 0
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in AMBIGUITY_GUARD:
                continue
            alias_tokens = set(alias.split("_"))
            if alias_tokens and alias_tokens.issubset(tokens):
                score = 85 + min(9, len(alias_tokens) * 3)
                if score > best_score:
                    best_field, best_score = field, score
            elif len(alias) >= 5 and alias in clean:
                if 80 > best_score:
                    best_field, best_score = field, 80

    if best_score:
        return _validate_against_values(best_field, best_score, values)

    # Fall back to what the values look like (§15 sample value analysis).
    inferred, confidence = _classify_by_values(values)
    if inferred:
        return inferred, confidence

    return "custom_metadata", 40


def _validate_against_values(field: str, confidence: int, values: List[str]) -> Tuple[str, int]:
    """Reject a name-based guess the data cannot support.

    A prompt field must contain prose. When the column holds integers or short
    scalars the header was misleading, so the guess is dropped rather than
    letting scene numbers end up in `visual_prompt`.
    """
    if field in TEXT_FIELDS and not _looks_like_prose(values):
        data_type = detect_data_type(values)
        if data_type in ("integer", "number", "boolean", "ratio", "empty"):
            return ("filename" if data_type == "integer" else "custom_metadata"), 55
        return "custom_metadata", 50
    return field, confidence


def _classify_by_values(values: List[str]) -> Tuple[Optional[str], int]:
    samples = [v for v in values if v][:25]
    if not samples:
        return None, 0

    if all(re.fullmatch(r"\d{1,2}\s*:\s*\d{1,2}", v) for v in samples):
        return "aspect_ratio", 75
    if all(re.fullmatch(r"\d+(\.\d+)?\s*(s|sec|secs|seconds)?", v, re.IGNORECASE) for v in samples):
        return "duration", 60
    if all(v.lower() in {"image", "voice", "video", "image_voice", "video_voice", "audio"} for v in samples):
        return "media_type", 80

    average_length = sum(len(v) for v in samples) / len(samples)
    if average_length > 120:
        return "visual_prompt", 55
    return None, 0


def detect_data_type(values: List[str]) -> str:
    samples = [v for v in values if v][:50]
    if not samples:
        return "empty"
    if all(re.fullmatch(r"-?\d+", v) for v in samples):
        return "integer"
    if all(re.fullmatch(r"-?\d+(\.\d+)?", v) for v in samples):
        return "number"
    if all(v.lower() in {"true", "false", "yes", "no"} for v in samples):
        return "boolean"
    if all(re.fullmatch(r"\d{1,2}:\d{1,2}", v) for v in samples):
        return "ratio"
    if all(v.startswith(("http://", "https://")) for v in samples):
        return "url"
    if sum(len(v) for v in samples) / len(samples) > 120:
        return "long_text"
    return "text"


def detect_encoding(contents: bytes) -> str:
    if contents.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    guess = chardet.detect(contents[:100_000]) or {}
    encoding = guess.get("encoding") or "utf-8"
    # chardet frequently reports ascii for utf-8 files with no high bytes yet.
    return "utf-8" if encoding.lower() == "ascii" else encoding


def detect_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in [",", ";", "\t", "|"]}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def analyze(contents: bytes, filename: str) -> Dict[str, Any]:
    """Full analysis of an uploaded CSV — no database writes."""
    encoding = detect_encoding(contents)
    try:
        text = contents.decode(encoding, errors="replace")
    except LookupError:
        encoding = "utf-8"
        text = contents.decode(encoding, errors="replace")

    if not text.strip():
        raise ValueError("The CSV file is empty")

    delimiter = detect_delimiter(text[:8192])

    # Read positionally rather than with DictReader: DictReader collapses
    # duplicate header names onto one key and buries surplus cells under None,
    # both of which would silently discard user data (§19).
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header_row = next(reader)
    except StopIteration:
        raise ValueError("No header row could be detected in this CSV")

    headers = _unique_headers(header_row)
    if not headers:
        raise ValueError("No header row could be detected in this CSV")

    raw_rows: List[Dict[str, str]] = []
    for values in reader:
        if not any((v or "").strip() for v in values):
            continue  # blank line
        row = {header: (values[i] if i < len(values) else "") for i, header in enumerate(headers)}
        # Cells past the last header are kept rather than thrown away.
        for extra_index in range(len(headers), len(values)):
            if (values[extra_index] or "").strip():
                row[f"extra_column_{extra_index + 1}"] = values[extra_index]
        raw_rows.append(row)

    if not raw_rows:
        raise ValueError("The CSV contains a header but no data rows")

    # Late-appearing overflow columns must exist on every row for a stable table.
    all_keys = {key for row in raw_rows for key in row}
    for key in sorted(all_keys - set(headers)):
        headers.append(key)
    for row in raw_rows:
        for header in headers:
            row.setdefault(header, "")

    # Pass 1 — classify every column independently.
    candidates: List[Dict[str, Any]] = []
    for index, header in enumerate(headers):
        values = [(row.get(header) or "").strip() for row in raw_rows]
        non_empty = [v for v in values if v]
        field, confidence = classify_column(header, values)
        candidates.append(
            {
                "column_index": index,
                "original_name": header,
                "detected_meaning": field,
                "confidence": confidence,
                "data_type": detect_data_type(values),
                "non_empty_count": len(non_empty),
                "example_value": (non_empty[0][:300] if non_empty else ""),
            }
        )

    # Pass 2 — resolve conflicts by confidence, not by column order.
    # Two columns can both look like `visual_prompt`; the better-matching one
    # must win. Resolving left-to-right would let a weak early match ("Image
    # Name" at 88%) beat an exact later one ("Visual Prompt" at 99%) and push
    # the real prompt into custom metadata.
    claimed: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        field = candidate["detected_meaning"]
        if field not in SCENE_FIELDS and field not in METADATA_FIELDS:
            continue
        holder = claimed.get(field)
        if holder is None or candidate["confidence"] > holder["confidence"]:
            if holder is not None:
                holder["demoted_from"] = field
            claimed[field] = candidate
        else:
            candidate["demoted_from"] = field

    columns: List[Dict[str, Any]] = []
    mapping: Dict[str, str] = {}
    for candidate in candidates:
        if candidate.get("demoted_from"):
            # Data is never dropped — it stays addressable as custom metadata (§19).
            candidate["detected_meaning"] = "custom_metadata"
            candidate["confidence"] = 40
        candidate["is_ambiguous"] = candidate["confidence"] < 80
        mapping[candidate["original_name"]] = candidate["detected_meaning"]
        columns.append(candidate)

    normalized = normalize_rows(raw_rows, mapping)

    return {
        "filename": filename,
        "file_size_bytes": len(contents),
        "encoding": encoding,
        "delimiter": delimiter,
        "total_rows": len(raw_rows),
        "columns_count": len(headers),
        "detected_columns": columns,
        "mapping": mapping,
        "raw_rows": raw_rows,
        **normalized,
    }


def duplicate_targets(mapping: Dict[str, str]) -> Dict[str, List[str]]:
    """Scene fields claimed by more than one column: {field: [columns]}.

    `custom_metadata` and `ignore` are excluded — many columns may share those.
    """
    by_field: Dict[str, List[str]] = {}
    for column, field in mapping.items():
        if field in SCENE_FIELDS or field in METADATA_FIELDS:
            by_field.setdefault(field, []).append(column)
    return {field: columns for field, columns in by_field.items() if len(columns) > 1}


def normalize_rows(raw_rows: List[Dict[str, str]], mapping: Dict[str, str]) -> Dict[str, Any]:
    """Apply a column mapping and produce canonical scene rows (§15, §18)."""
    valid: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []
    seen_ids = set()

    has_images = has_voiceovers = has_videos = has_master = False

    mapped_targets = set(mapping.values())
    visual_mapped = "visual_prompt" in mapped_targets
    script_mapped = "voiceover_script" in mapped_targets

    for index, row in enumerate(raw_rows, start=1):
        scene: Dict[str, Any] = {"custom_metadata": {}}
        non_empty: Dict[str, str] = {}

        for header, value in row.items():
            text = (value or "").strip()
            target = mapping.get(header, "custom_metadata")
            if target == "ignore":
                continue
            if text:
                non_empty[header] = text
            if target in METADATA_FIELDS or target == "custom_metadata":
                key = target if target in METADATA_FIELDS else header
                scene["custom_metadata"][key] = text
            else:
                scene[target] = text

        # No visual column at all: use the longest free-text value so an
        # arbitrary CSV still produces something generatable (§14).
        if not visual_mapped and not scene.get("visual_prompt") and not script_mapped and non_empty:
            longest = max(non_empty, key=lambda k: len(non_empty[k]))
            if len(non_empty[longest]) >= 20:
                scene["visual_prompt"] = non_empty[longest]

        scene_id = scene.get("scene_number") or str(index)
        if scene_id in seen_ids:
            scene_id = f"{scene_id}_{index}"
        seen_ids.add(scene_id)
        scene["id"] = scene_id
        scene["scene_number"] = scene_id

        ratio = (scene.get("aspect_ratio") or "").replace(" ", "")
        scene["aspect_ratio"] = ratio if ratio in VALID_ASPECT_RATIOS else "16:9"

        duration = scene.get("duration")
        if duration:
            match = re.search(r"\d+(\.\d+)?", str(duration))
            scene["duration"] = float(match.group()) if match else None
        else:
            scene.pop("duration", None)

        media_type = (scene.get("media_type") or "").strip().lower().replace("-", "_")
        if not media_type:
            if scene.get("video_prompt") and scene.get("voiceover_script"):
                media_type = "video_voice"
            elif scene.get("video_prompt"):
                media_type = "video"
            elif scene.get("visual_prompt") and scene.get("voiceover_script"):
                media_type = "image_voice"
            elif scene.get("visual_prompt"):
                media_type = "image"
            elif scene.get("voiceover_script"):
                media_type = "voice"
        scene["media_type"] = media_type or "image"

        if scene.get("visual_prompt"):
            has_images = True
        if scene.get("voiceover_script"):
            has_voiceovers = True
        if scene.get("video_prompt") or scene["media_type"].startswith("video"):
            has_videos = True
        if scene.get("master_prompt"):
            has_master = True

        if scene.get("visual_prompt") or scene.get("voiceover_script") or scene.get("video_prompt"):
            valid.append(scene)
        else:
            invalid.append(
                {
                    "row_number": index,
                    "data": scene,
                    "errors": ["No visual prompt, video prompt, or voiceover script found in this row"],
                }
            )

    return {
        "valid_rows": valid,
        "invalid_rows": invalid,
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "detected_media_requirements": {
            "has_images": has_images,
            "has_voiceovers": has_voiceovers,
            "has_videos": has_videos,
        },
        "has_master_prompt": has_master,
    }
