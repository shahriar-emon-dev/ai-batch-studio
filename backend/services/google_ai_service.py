"""Google AI provider implementation (proposal §27, §28, §29, §48, §50).

Every call classifies its failure so the orchestrator can decide between
retrying, rotating to another API profile, or marking a task UNSUPPORTED.
Raw API keys are never logged.
"""

import asyncio
import base64
import logging
import struct
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

GENAI_BASE = "https://generativelanguage.googleapis.com/v1beta"
TTS_BASE = "https://texttospeech.googleapis.com/v1"


# ---------------------------------------------------------------------------
# Error taxonomy (proposal §48 — Error Classifier)
# ---------------------------------------------------------------------------

class ProviderError(Exception):
    """Base class. `category` is persisted to error_logs."""

    category = "PROVIDER_ERROR"
    retryable = False


class AuthError(ProviderError):
    """Invalid / revoked / unauthorized API key. Never retried."""

    category = "INVALID_API_KEY"
    retryable = False


class QuotaExceededError(ProviderError):
    """Daily or project quota exhausted. Profile is parked, then rotated."""

    category = "QUOTA_EXCEEDED"
    retryable = True


class RateLimitException(ProviderError):
    """Short-term rate limit. Backoff + rotate."""

    category = "RATE_LIMITED"
    retryable = True


class NetworkError(ProviderError):
    category = "NETWORK_ERROR"
    retryable = True


class TimeoutError_(ProviderError):
    category = "TIMEOUT"
    retryable = True


class ProviderUnavailableException(ProviderError):
    """Model/feature not available to this account — surfaced as UNSUPPORTED."""

    category = "UNSUPPORTED"
    retryable = False


def _extract_api_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return (response.text or "")[:500]
    if isinstance(payload, dict):
        err = payload.get("error") or {}
        if isinstance(err, dict):
            return str(err.get("message") or payload)[:500]
    return str(payload)[:500]


def _raise_for_status(response: httpx.Response, feature: str) -> None:
    """Translate an HTTP error response into the taxonomy above."""
    if response.status_code < 400:
        return

    message = _extract_api_error(response)
    lowered = message.lower()
    status = response.status_code

    if status == 429:
        # Google returns 429 for both burst rate limits and exhausted quota.
        if "quota" in lowered or "exhausted" in lowered or "billing" in lowered:
            raise QuotaExceededError(f"{feature}: quota exceeded — {message}")
        raise RateLimitException(f"{feature}: rate limited — {message}")

    if status in (401, 403):
        if "not enabled" in lowered or "has not been used" in lowered or "disabled" in lowered:
            raise ProviderUnavailableException(
                f"{feature}: the required Google API is not enabled for this key — {message}"
            )
        raise AuthError(f"{feature}: authentication failed — {message}")

    if status == 404 or "not found" in lowered or "is not supported" in lowered:
        raise ProviderUnavailableException(f"{feature}: model unavailable — {message}")

    if status == 400 and ("not supported" in lowered or "unsupported" in lowered):
        raise ProviderUnavailableException(f"{feature}: unsupported request — {message}")

    if status >= 500:
        err = ProviderError(f"{feature}: provider error {status} — {message}")
        err.retryable = True
        raise err

    raise ProviderError(f"{feature}: request failed ({status}) — {message}")


def _wrap_transport_error(exc: Exception, feature: str) -> ProviderError:
    if isinstance(exc, httpx.TimeoutException):
        return TimeoutError_(f"{feature}: request timed out")
    if isinstance(exc, httpx.TransportError):
        return NetworkError(f"{feature}: network error — {exc}")
    return ProviderError(f"{feature}: {exc}")


# ---------------------------------------------------------------------------
# Image generation (proposal §27)
# ---------------------------------------------------------------------------

def _first_inline_image(data: Dict[str, Any]) -> Optional[bytes]:
    for candidate in data.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    return None


def _blocked_reason(data: Dict[str, Any]) -> Optional[str]:
    feedback = data.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        return str(feedback["blockReason"])
    for candidate in data.get("candidates") or []:
        reason = candidate.get("finishReason")
        if reason and reason not in ("STOP", "MAX_TOKENS"):
            return str(reason)
    return None


def image_model_chain(preferred: Optional[str] = None) -> List[str]:
    """Ordered, de-duplicated list of image models to attempt."""
    chain = [preferred or settings.image_model]
    chain += [m.strip() for m in (settings.image_model_fallbacks or "").split(",") if m.strip()]
    seen, ordered = set(), []
    for model in chain:
        if model and model not in seen:
            seen.add(model)
            ordered.append(model)
    return ordered


async def generate_image(
    api_key: str,
    prompt: str,
    aspect_ratio: str = "16:9",
    negative_prompt: str = "",
    model: Optional[str] = None,
) -> bytes:
    """Generate a single image and return raw PNG/JPEG bytes.

    Walks the configured model chain, moving on only when a model is
    *unavailable* to this account. Quota, rate-limit and auth failures abort
    immediately: they apply to the whole account, so retrying other models
    would burn requests and mask the real reason.
    """
    chain = image_model_chain(model)
    unavailable: List[str] = []

    for candidate in chain:
        try:
            return await _generate_image_gemini(api_key, candidate, prompt, aspect_ratio, negative_prompt)
        except ProviderUnavailableException as exc:
            unavailable.append(f"{candidate}: {exc}")
            logger.info("Image model %s unavailable, trying next in chain", candidate)
            continue

    # Last resort: the Imagen predict endpoint, if this account still has it.
    try:
        text = f"{prompt}\n\nAvoid: {negative_prompt}" if negative_prompt else prompt
        return await _generate_image_imagen(api_key, text, aspect_ratio)
    except ProviderUnavailableException as exc:
        unavailable.append(f"{settings.imagen_model}: {exc}")

    raise ProviderUnavailableException(
        "No image model is available to this API key. Tried: " + " | ".join(unavailable)
    )


async def _generate_image_gemini(
    api_key: str, model: str, prompt: str, aspect_ratio: str, negative_prompt: str
) -> bytes:
    url = f"{GENAI_BASE}/models/{model}:generateContent?key={api_key}"

    text = prompt
    if negative_prompt:
        text = f"{prompt}\n\nAvoid: {negative_prompt}"

    payload: Dict[str, Any] = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": aspect_ratio or "16:9"},
        },
    }

    try:
        async with httpx.AsyncClient(timeout=settings.image_timeout) as client:
            response = await client.post(url, json=payload)

            # Older image models reject imageConfig — retry once without it.
            if response.status_code == 400 and "imageConfig" in (_extract_api_error(response) or ""):
                payload["generationConfig"].pop("imageConfig", None)
                response = await client.post(url, json=payload)

            _raise_for_status(response, f"Image generation ({model})")
            data = response.json()
    except ProviderError:
        raise
    except Exception as exc:
        raise _wrap_transport_error(exc, f"Image generation ({model})") from exc

    image = _first_inline_image(data)
    if image:
        return image

    blocked = _blocked_reason(data)
    if blocked:
        raise ProviderError(f"Image generation refused by safety filter ({blocked})")
    raise ProviderError("Image generation returned no image data")


async def _generate_image_imagen(api_key: str, prompt: str, aspect_ratio: str) -> bytes:
    url = f"{GENAI_BASE}/models/{settings.imagen_model}:predict?key={api_key}"
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {"sampleCount": 1, "aspectRatio": aspect_ratio or "16:9"},
    }

    try:
        async with httpx.AsyncClient(timeout=settings.image_timeout) as client:
            response = await client.post(url, json=payload)
            _raise_for_status(response, "Image generation (Imagen)")
            data = response.json()
    except ProviderError:
        raise
    except Exception as exc:
        raise _wrap_transport_error(exc, "Image generation (Imagen)") from exc

    for prediction in data.get("predictions") or []:
        encoded = prediction.get("bytesBase64Encoded")
        if encoded:
            return base64.b64decode(encoded)

    raise ProviderError("Imagen returned no image data")


# ---------------------------------------------------------------------------
# Voiceover generation (proposal §28)
# ---------------------------------------------------------------------------

def _pcm_to_wav(pcm: bytes, sample_rate: int = 24000, channels: int = 1, bits: int = 16) -> bytes:
    """Gemini TTS returns headerless PCM; browsers need a RIFF header."""
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits)
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


async def generate_speech(
    api_key: str,
    text: str,
    voice: str = "",
    language: str = "en-US",
    speed: float = 1.0,
    pitch: float = 0.0,
) -> Tuple[bytes, str]:
    """Synthesize speech. Returns (audio_bytes, file_extension).

    Cloud Text-to-Speech is preferred because it exposes named voices and
    speaking rate. If that API is not enabled for the key we fall back to the
    Gemini TTS model so voiceovers still work with a plain AI Studio key.
    """
    voice_config: Dict[str, Any] = {"languageCode": language or "en-US"}
    if voice:
        voice_config["name"] = voice

    payload = {
        "input": {"text": text},
        "voice": voice_config,
        "audioConfig": {
            "audioEncoding": "MP3",
            "speakingRate": max(0.25, min(4.0, float(speed or 1.0))),
            "pitch": max(-20.0, min(20.0, float(pitch or 0.0))),
        },
    }

    try:
        async with httpx.AsyncClient(timeout=settings.audio_timeout) as client:
            response = await client.post(f"{TTS_BASE}/text:synthesize?key={api_key}", json=payload)

            # A named voice that does not exist for the language is a 400 — retry
            # once with the language default rather than failing the scene.
            if response.status_code == 400 and voice:
                payload["voice"] = {"languageCode": language or "en-US"}
                response = await client.post(f"{TTS_BASE}/text:synthesize?key={api_key}", json=payload)

            if response.status_code in (401, 403):
                logger.info("Cloud TTS unavailable for this key, falling back to Gemini TTS")
                return await _generate_speech_gemini(api_key, text, voice)

            _raise_for_status(response, "Voiceover generation")
            data = response.json()
    except ProviderError:
        raise
    except Exception as exc:
        raise _wrap_transport_error(exc, "Voiceover generation") from exc

    audio = data.get("audioContent")
    if not audio:
        raise ProviderError("Voiceover generation returned no audio content")
    return base64.b64decode(audio), "mp3"


async def _generate_speech_gemini(api_key: str, text: str, voice: str = "") -> Tuple[bytes, str]:
    url = f"{GENAI_BASE}/models/{settings.tts_model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice or settings.default_gemini_voice}
                }
            },
        },
    }

    try:
        async with httpx.AsyncClient(timeout=settings.audio_timeout) as client:
            response = await client.post(url, json=payload)
            _raise_for_status(response, "Voiceover generation (Gemini TTS)")
            data = response.json()
    except ProviderError:
        raise
    except Exception as exc:
        raise _wrap_transport_error(exc, "Voiceover generation (Gemini TTS)") from exc

    pcm = _first_inline_image(data)  # same inlineData extraction, audio payload
    if not pcm:
        raise ProviderError("Gemini TTS returned no audio content")
    return _pcm_to_wav(pcm), "wav"


async def list_voices(api_key: str, language: str = "") -> List[Dict[str, Any]]:
    """Real voice list from Google Cloud TTS (empty list if not enabled)."""
    url = f"{TTS_BASE}/voices?key={api_key}"
    if language:
        url += f"&languageCode={language}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            if response.status_code in (401, 403, 404):
                return []
            _raise_for_status(response, "Voice list")
            data = response.json()
    except ProviderError:
        raise
    except Exception as exc:
        raise _wrap_transport_error(exc, "Voice list") from exc

    return [
        {
            "name": voice.get("name"),
            "language_codes": voice.get("languageCodes", []),
            "gender": voice.get("ssmlGender"),
            "sample_rate": voice.get("naturalSampleRateHertz"),
        }
        for voice in data.get("voices") or []
    ]


# ---------------------------------------------------------------------------
# Video generation (proposal §29)
# ---------------------------------------------------------------------------

async def generate_video(
    api_key: str,
    prompt: str,
    aspect_ratio: str = "16:9",
    duration: Optional[float] = None,
    negative_prompt: str = "",
) -> bytes:
    """Generate a video with Veo via the long-running predict endpoint.

    Raises ProviderUnavailableException when the account has no Veo access, so
    the task is recorded as UNSUPPORTED instead of a fake success (§29).
    """
    if not settings.video_generation_enabled:
        raise ProviderUnavailableException(
            "Video generation is disabled. Enable VIDEO_GENERATION_ENABLED and configure a Veo-capable key."
        )

    model = settings.video_model
    start_url = f"{GENAI_BASE}/models/{model}:predictLongRunning?key={api_key}"

    parameters: Dict[str, Any] = {"aspectRatio": aspect_ratio or "16:9"}
    if duration:
        parameters["durationSeconds"] = int(max(1, min(60, float(duration))))
    if negative_prompt:
        parameters["negativePrompt"] = negative_prompt

    try:
        async with httpx.AsyncClient(timeout=settings.video_timeout) as client:
            response = await client.post(
                start_url, json={"instances": [{"prompt": prompt}], "parameters": parameters}
            )
            _raise_for_status(response, "Video generation")
            operation = response.json()

            name = operation.get("name")
            if not name:
                raise ProviderError("Video generation did not return an operation name")

            deadline = asyncio.get_event_loop().time() + settings.video_poll_timeout
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(settings.video_poll_interval)
                poll = await client.get(f"{GENAI_BASE}/{name}?key={api_key}")
                _raise_for_status(poll, "Video generation (poll)")
                operation = poll.json()
                if operation.get("done"):
                    break
            else:
                raise TimeoutError_("Video generation timed out while waiting for Veo")

            if operation.get("error"):
                raise ProviderError(f"Video generation failed — {operation['error'].get('message')}")

            uri = _extract_video_uri(operation)
            if not uri:
                raise ProviderError("Video generation completed without a downloadable video")

            download = await client.get(
                uri if "key=" in uri else f"{uri}{'&' if '?' in uri else '?'}key={api_key}"
            )
            _raise_for_status(download, "Video download")
            return download.content
    except ProviderError:
        raise
    except Exception as exc:
        raise _wrap_transport_error(exc, "Video generation") from exc


def _extract_video_uri(operation: Dict[str, Any]) -> Optional[str]:
    response = operation.get("response") or {}
    containers = [
        response.get("generateVideoResponse") or {},
        response,
    ]
    for container in containers:
        samples = container.get("generatedSamples") or container.get("videos") or []
        for sample in samples:
            video = sample.get("video") or sample
            uri = video.get("uri") or video.get("url")
            if uri:
                return uri
    return None


# ---------------------------------------------------------------------------
# Connection test (proposal §12 — must reflect the real API result)
# ---------------------------------------------------------------------------

async def test_connection(api_key: str) -> Dict[str, Any]:
    """Make a real request and report exactly what the provider said."""
    url = f"{GENAI_BASE}/models?key={api_key}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
    except httpx.TimeoutException:
        return {"ok": False, "status": "TIMEOUT", "message": "Connection to Google AI timed out"}
    except httpx.TransportError as exc:
        return {"ok": False, "status": "NETWORK_ERROR", "message": f"Network error: {exc}"}

    if response.status_code == 200:
        models = [m.get("name", "") for m in response.json().get("models") or []]
        return {
            "ok": True,
            "status": "SUCCESS",
            "message": f"Connection successful — {len(models)} models available",
            "model_count": len(models),
            "image_model_available": any(settings.image_model in m for m in models),
            "video_model_available": any(settings.video_model in m for m in models),
        }

    message = _extract_api_error(response)
    if response.status_code == 429:
        status = "QUOTA_EXCEEDED" if "quota" in message.lower() else "RATE_LIMITED"
    elif response.status_code in (401, 403):
        status = "INVALID_API_KEY"
    elif response.status_code >= 500:
        status = "SERVICE_UNAVAILABLE"
    else:
        status = "FAILED"

    return {"ok": False, "status": status, "message": message}
