"""Dynamic prompt composition (proposal §25, §26, §27, §28, §29).

Only fields that actually exist on the scene are included, so a CSV with three
columns and a CSV with twenty both produce a coherent prompt.
"""

from typing import Any, Dict, Optional, Tuple

# Custom/unknown CSV columns worth folding into a visual prompt, in the order
# they read best. Everything else stays in custom_metadata untouched (§19).
VISUAL_METADATA_LABELS = {
    "character": "Character",
    "characters": "Characters",
    "subject": "Subject",
    "location": "Location",
    "setting": "Setting",
    "environment": "Environment",
    "background": "Background",
    "camera": "Camera",
    "camera_angle": "Camera angle",
    "camera_movement": "Camera movement",
    "shot_type": "Shot type",
    "lighting": "Lighting",
    "mood": "Mood",
    "emotion": "Emotion",
    "atmosphere": "Atmosphere",
    "color_palette": "Color palette",
    "composition": "Composition",
    "time_of_day": "Time of day",
    "weather": "Weather",
    "historical_period": "Period",
    "custom_style": "Style",
}

VIDEO_METADATA_LABELS = {
    "camera": "Camera",
    "camera_movement": "Camera movement",
    "motion": "Motion",
    "action": "Action",
    "transition": "Transition",
    "pacing": "Pacing",
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("", "none", "null", "n/a", "-") else text


def _metadata_parts(scene: Dict[str, Any], labels: Dict[str, str]) -> list:
    custom = scene.get("custom_metadata") or {}
    if not isinstance(custom, dict):
        return []

    parts = []
    normalized = {str(k).strip().lower().replace(" ", "_").replace("-", "_"): v for k, v in custom.items()}
    for key, label in labels.items():
        value = _clean(normalized.get(key))
        if value:
            parts.append(f"{label}: {value}")
    return parts


def compose_image_prompt(scene: Dict[str, Any]) -> str:
    """Master prompt + scene prompt + every relevant descriptor present (§27)."""
    parts = []

    master = _clean(scene.get("master_prompt"))
    if master:
        parts.append(master)

    visual = _clean(scene.get("enhanced_visual_prompt")) or _clean(scene.get("visual_prompt"))
    if visual:
        parts.append(visual)

    style = _clean(scene.get("style"))
    if style:
        parts.append(f"Style: {style}")

    tone = _clean(scene.get("tone"))
    if tone:
        parts.append(f"Tone: {tone}")

    parts.extend(_metadata_parts(scene, VISUAL_METADATA_LABELS))
    return ", ".join(parts)


def compose_video_prompt(scene: Dict[str, Any]) -> str:
    """Video prompt + master prompt + style/duration hints (§29)."""
    parts = []

    master = _clean(scene.get("master_prompt"))
    if master:
        parts.append(master)

    video = _clean(scene.get("video_prompt"))
    # Fall back to the visual prompt so an image-oriented CSV can still drive video.
    video = video or _clean(scene.get("visual_prompt"))
    if video:
        parts.append(video)

    style = _clean(scene.get("style"))
    if style:
        parts.append(f"Style: {style}")

    parts.extend(_metadata_parts(scene, VIDEO_METADATA_LABELS))
    return ", ".join(parts)


def compose_voice_request(
    scene: Dict[str, Any], defaults: Optional[Dict[str, Any]] = None
) -> Tuple[str, Dict[str, Any]]:
    """Return (script, tts_options) built from scene values then user defaults (§28)."""
    defaults = defaults or {}

    script = _clean(scene.get("enhanced_voiceover_script")) or _clean(scene.get("voiceover_script"))

    # Tone is not a TTS parameter, but prefixing it as a style cue meaningfully
    # changes delivery on Gemini TTS and is harmless for Cloud TTS.
    tone = _clean(scene.get("tone"))
    if tone and script:
        script = f"Say in a {tone.lower()} tone: {script}"

    speed = scene.get("speaking_speed") or defaults.get("default_speech_speed") or 1.0
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        speed = 1.0

    options = {
        "voice": _clean(scene.get("voice_name")) or _clean(scene.get("voice")) or _clean(defaults.get("default_voice")),
        "language": _clean(scene.get("language")) or _clean(defaults.get("default_language")) or "en-US",
        "speed": speed,
        "pitch": 0.0,
    }
    return script, options


def negative_prompt_for(scene: Dict[str, Any], defaults: Optional[Dict[str, Any]] = None) -> str:
    defaults = defaults or {}
    return _clean(scene.get("negative_prompt")) or _clean(defaults.get("default_negative_prompt"))
