"""Error classification + API profile rotation (simulated provider, no network)."""
import asyncio
import datetime
import os
import sys

sys.path.insert(0, r"e:\video automation\ai_batch_studio")

import httpx

from backend.services import api_profile_service as aps
from backend.services import google_ai_service as g

failures = []


def check(label, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + ("" if condition else f"  {detail}"))
    if not condition:
        failures.append(label)


def response(status, message):
    return httpx.Response(status, json={"error": {"message": message}},
                          request=httpx.Request("POST", "https://example.invalid"))


print("\n[1] HTTP status -> error taxonomy (§48)")
cases = [
    (429, "Quota exceeded for quota metric 'Generate requests'", g.QuotaExceededError, "QUOTA_EXCEEDED", True),
    (429, "Resource has been exhausted", g.QuotaExceededError, "QUOTA_EXCEEDED", True),
    (429, "Too many requests, please slow down", g.RateLimitException, "RATE_LIMITED", True),
    (401, "API key not valid", g.AuthError, "INVALID_API_KEY", False),
    (403, "Requests to this API generativelanguage are not enabled", g.ProviderUnavailableException, "UNSUPPORTED", False),
    (404, "models/veo-x is not found", g.ProviderUnavailableException, "UNSUPPORTED", False),
    (400, "Response modality IMAGE is not supported", g.ProviderUnavailableException, "UNSUPPORTED", False),
    (400, "Invalid argument: prompt too long", g.ProviderError, "PROVIDER_ERROR", False),
    (503, "The service is currently unavailable", g.ProviderError, "PROVIDER_ERROR", True),
]
for status, message, expected_type, expected_category, retryable in cases:
    try:
        g._raise_for_status(response(status, message), "Test")
        check(f"{status} '{message[:32]}'", False, "no exception raised")
    except Exception as exc:
        ok = isinstance(exc, expected_type) and getattr(exc, "category", None) == expected_category
        ok = ok and bool(getattr(exc, "retryable", False)) == retryable
        check(f"{status} -> {expected_type.__name__}/{expected_category} retryable={retryable}", ok,
              f"got {type(exc).__name__}/{getattr(exc,'category',None)}/{getattr(exc,'retryable',None)}")

check("2xx raises nothing", g._raise_for_status(response(200, ""), "Test") is None)

print("\n[2] Transport failures are classified")
check("timeout -> TIMEOUT",
      g._wrap_transport_error(httpx.ReadTimeout("t"), "Test").category == "TIMEOUT")
check("connect error -> NETWORK_ERROR",
      g._wrap_transport_error(httpx.ConnectError("c"), "Test").category == "NETWORK_ERROR")

print("\n[3] Response parsing")
inline = {"candidates": [{"content": {"parts": [{"inlineData": {"data": "aGVsbG8="}}]}}]}
check("inline image extracted", g._first_inline_image(inline) == b"hello")
check("snake_case inline handled",
      g._first_inline_image({"candidates": [{"content": {"parts": [{"inline_data": {"data": "aGVsbG8="}}]}}]}) == b"hello")
check("no image -> None", g._first_inline_image({"candidates": []}) is None)
check("safety block detected",
      g._blocked_reason({"promptFeedback": {"blockReason": "SAFETY"}}) == "SAFETY")
check("finish reason detected",
      g._blocked_reason({"candidates": [{"finishReason": "IMAGE_SAFETY"}]}) == "IMAGE_SAFETY")
check("normal finish is not a block",
      g._blocked_reason({"candidates": [{"finishReason": "STOP"}]}) is None)

video_op = {"response": {"generateVideoResponse": {"generatedSamples": [
    {"video": {"uri": "https://example.invalid/v.mp4"}}]}}}
check("video uri extracted", g._extract_video_uri(video_op) == "https://example.invalid/v.mp4")
check("missing video uri -> None", g._extract_video_uri({"response": {}}) is None)

print("\n[4] PCM is wrapped as playable WAV")
wav = g._pcm_to_wav(b"\x00\x01" * 100)
check("RIFF header", wav[:4] == b"RIFF" and wav[8:12] == b"WAVE", wav[:12])
check("payload preserved", len(wav) == 44 + 200, len(wav))

print("\n[5] Profile pool rotation (§13)")


def make_pool(n=3):
    return aps.ApiProfilePool([
        {"id": i, "profile_name": f"P{i}", "api_key": f"key-{i}", "priority": 0,
         "request_count": 0, "success_count": 0, "failure_count": 0, "unavailable_until_dt": None}
        for i in range(1, n + 1)
    ])


async def scenario_round_robin():
    pool = make_pool()
    picked = [(await pool.acquire())["id"] for _ in range(4)]
    return picked


check("round-robins across profiles", asyncio.run(scenario_round_robin()) == [1, 2, 3, 1])


async def scenario_quota_parks_profile():
    pool = make_pool(2)
    first = await pool.acquire()
    await pool.report_failure(first, g.QuotaExceededError("quota"))
    # The parked profile must not come back until its cooldown expires.
    picks = {(await pool.acquire())["id"] for _ in range(4)}
    return first["id"], picks, pool.available_count()


parked_id, picks, available = asyncio.run(scenario_quota_parks_profile())
check("quota parks the profile", parked_id not in picks, (parked_id, picks))
check("one profile remains available", available == 1, available)

calls = []


async def failing_then_ok(api_key, *_args, **_kwargs):
    calls.append(api_key)
    if api_key == "key-1":
        raise g.QuotaExceededError("Quota exceeded")
    return b"generated-bytes"


async def scenario_rotation_recovers():
    calls.clear()
    pool = make_pool(2)
    result, profile = await aps.call_with_rotation(pool, failing_then_ok, "a prompt")
    return result, profile, list(calls)


result, profile, attempted = asyncio.run(scenario_rotation_recovers())
check("rotation produced a result", result == b"generated-bytes", result)
check("second profile was used", profile["id"] == 2, profile["id"])
check("both keys attempted in order", attempted == ["key-1", "key-2"], attempted)


async def always_quota(api_key, *_a, **_k):
    raise g.QuotaExceededError("Quota exceeded")


async def scenario_all_exhausted():
    pool = make_pool(2)
    try:
        await aps.call_with_rotation(pool, always_quota, "p")
        return "no-error"
    except g.QuotaExceededError:
        return "quota-error"
    except aps.NoAvailableProfileError:
        return "no-profile-error"


check("all profiles exhausted surfaces an error",
      asyncio.run(scenario_all_exhausted()) in ("quota-error", "no-profile-error"))


async def unsupported_op(api_key, *_a, **_k):
    raise g.ProviderUnavailableException("Veo not available")


async def scenario_unsupported_not_retried():
    calls.clear()

    async def counted(api_key, *a, **k):
        calls.append(api_key)
        return await unsupported_op(api_key)

    pool = make_pool(3)
    try:
        await aps.call_with_rotation(pool, counted, "p")
    except g.ProviderUnavailableException:
        pass
    return len(calls)


check("non-retryable error stops after one attempt",
      asyncio.run(scenario_unsupported_not_retried()) == 1,
      asyncio.run(scenario_unsupported_not_retried()))


async def scenario_cooldown_expiry():
    pool = make_pool(1)
    profile = await pool.acquire()
    profile["unavailable_until_dt"] = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
    return (await pool.acquire())["id"]


check("expired cooldown returns the profile to rotation", asyncio.run(scenario_cooldown_expiry()) == 1)

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
