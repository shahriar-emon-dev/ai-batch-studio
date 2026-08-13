"""Regression tests — one per bug fixed after the first implementation pass.

Each check corresponds to a defect that was found by auditing the code, so a
failure here means a specific known bug has come back.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services import api_profile_service as aps
from backend.services import asset_service, csv_service, google_ai_service as g
from backend.services.generation_service import _merge_is_stale

failures = []


def check(label, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + ("" if condition else f"  {detail}"))
    if not condition:
        failures.append(label)


print("\n[BUG] Duplicate CSV headers dropped one of the columns")
dupes = (
    "id,prompt,prompt,notes\n"
    "1,First prompt text here,Second prompt text here,some notes\n"
).encode()
result = csv_service.analyze(dupes, "dupes.csv")
names = [c["original_name"] for c in result["detected_columns"]]
check("both duplicate columns kept", len(names) == 4, names)
check("second is renamed, not merged", "prompt" in names and "prompt_2" in names, names)
row = result["raw_rows"][0]
check("first column's data intact", row["prompt"] == "First prompt text here", row)
check("second column's data intact", row["prompt_2"] == "Second prompt text here", row)

print("\n[BUG] 'Image Name' stole visual_prompt from the real 'Visual Prompt' column")
# Exact header set from diner_wars_17min_FIXED.csv, which imported 81 scenes
# whose visual_prompt held "1", "2", "3" instead of the image prompt.
real = (
    "Image Name,Voiceover+,Visual Prompt,On-Screen Text\n"
    "1,\"You are 19 when the war begins, and the loudest thing in your life is still the deep fryer.\","
    "\"2D minimalist stickman animation, thick clean black outlines, simple circular head with small dot eyes\","
    "\"HOOK TEXT: 'He didn't know a WAFFLE would ruin his life...'\"\n"
    "2,\"Before sunrise, grease pops and the griddle hisses awake in the dark.\","
    "\"2D minimalist stickman animation, wide shot of a diner interior at dawn, muted palette\","
    "\"ON-SCREEN: The Boring Part\"\n"
).encode()
real_result = csv_service.analyze(real, "diner_wars.csv")
real_map = {c["original_name"]: c["detected_meaning"] for c in real_result["detected_columns"]}
conf = {c["original_name"]: c["confidence"] for c in real_result["detected_columns"]}

check("'Visual Prompt' wins visual_prompt", real_map["Visual Prompt"] == "visual_prompt", real_map)
check("'Visual Prompt' is high confidence", conf["Visual Prompt"] >= 95, conf)
check("'Image Name' is NOT visual_prompt", real_map["Image Name"] != "visual_prompt", real_map)
check("'Image Name' treated as filename", real_map["Image Name"] == "filename", real_map)
check("'Voiceover+' wins voiceover_script", real_map["Voiceover+"] == "voiceover_script", real_map)
check("'On-Screen Text' preserved as metadata", real_map["On-Screen Text"] == "custom_metadata", real_map)

scene1 = real_result["valid_rows"][0]
check("scene 1 visual_prompt is the image prompt",
      scene1["visual_prompt"].startswith("2D minimalist stickman"), scene1["visual_prompt"][:40])
check("scene 1 visual_prompt is not a number", scene1["visual_prompt"] != "1", scene1["visual_prompt"])
check("scene 1 voiceover intact", scene1["voiceover_script"].startswith("You are 19"), scene1["voiceover_script"][:30])
check("scene 1 media type inferred", scene1["media_type"] == "image_voice", scene1["media_type"])
check("on-screen text kept in metadata",
      "On-Screen Text" in scene1["custom_metadata"], scene1["custom_metadata"])

print("\n[BUG] Conflicts resolved by column order instead of confidence")
# Weak fuzzy match on the left, exact match on the right.
ordered = (
    "Image Ref,Scene Description,Visual Prompt\n"
    "A1,Some short note,\"A sweeping cinematic vista of a mountain range at golden hour, volumetric light\"\n"
).encode()
ordered_map = {c["original_name"]: c["detected_meaning"]
               for c in csv_service.analyze(ordered, "o.csv")["detected_columns"]}
check("exact match beats earlier fuzzy match",
      ordered_map["Visual Prompt"] == "visual_prompt", ordered_map)
check("loser demoted to custom_metadata, not dropped",
      ordered_map["Scene Description"] == "custom_metadata", ordered_map)

print("\n[BUG] Prompt fields accepting non-prose columns")
numeric = ("Visual Prompt,Voiceover\n1,\"A long narration line that clearly carries the spoken script content.\"\n"
           "2,\"Another long narration line that clearly carries spoken script content here.\"\n").encode()
numeric_map = {c["original_name"]: c["detected_meaning"]
               for c in csv_service.analyze(numeric, "n.csv")["detected_columns"]}
check("integer column rejected as visual_prompt",
      numeric_map["Visual Prompt"] != "visual_prompt", numeric_map)

print("\n[BUG] Cells past the last header were discarded")
ragged = (
    "id,prompt\n"
    "1,A dramatic sunrise over the mountains,orphaned value,another orphan\n"
).encode()
ragged_result = csv_service.analyze(ragged, "ragged.csv")
ragged_row = ragged_result["raw_rows"][0]
check("overflow cells preserved",
      "orphaned value" in ragged_row.values() and "another orphan" in ragged_row.values(), ragged_row)
overflow_headers = [h for h in ragged_row if h.startswith("extra_column_")]
check("overflow columns are addressable", len(overflow_headers) == 2, overflow_headers)
check("overflow columns appear in the analysis",
      all(any(c["original_name"] == h for c in ragged_result["detected_columns"]) for h in overflow_headers))

print("\n[BUG] Empty header cells produced unusable blank column names")
blank = "id,,prompt\n1,x,A long descriptive prompt for the scene\n".encode()
blank_result = csv_service.analyze(blank, "blank.csv")
blank_names = [c["original_name"] for c in blank_result["detected_columns"]]
check("blank header given a stable name", "column_2" in blank_names, blank_names)

print("\n[BUG] Two columns mapped to one field silently lost data")
mapping = {"a": "visual_prompt", "b": "visual_prompt", "c": "custom_metadata", "d": "custom_metadata"}
dup_targets = csv_service.duplicate_targets(mapping)
check("duplicate scene field detected", "visual_prompt" in dup_targets, dup_targets)
check("both offending columns reported", sorted(dup_targets["visual_prompt"]) == ["a", "b"], dup_targets)
check("custom_metadata may repeat", "custom_metadata" not in dup_targets, dup_targets)
check("clean mapping reports nothing",
      csv_service.duplicate_targets({"a": "visual_prompt", "b": "tone"}) == {})

print("\n[BUG] Media URLs were enumerable (/output/images/12.png)")
_, url_a = asset_service.storage_paths("image", 4242, "png")
check("filename is not the bare scene id", not url_a.endswith("/4242.png"), url_a)
check("filename still starts with the scene id", "/4242_" in url_a, url_a)
tokens = {asset_service.storage_token("image", i) for i in range(1, 40)}
check("tokens are unique across scenes", len(tokens) == 39, len(tokens))
check("token is hex of expected length", all(len(t) == 16 for t in tokens))

print("\n[BUG] Stale merged video reused after inputs were regenerated")
image_abs, _ = asset_service.write_asset_file("image", 91001, "png", b"\x89PNG" + b"0" * 64)
audio_abs, _ = asset_service.write_asset_file("voiceover", 91001, "mp3", b"ID3" + b"1" * 64)
merged_abs, _ = asset_service.write_asset_file("merged", 91001, "mp4", b"MP4" + b"2" * 64)
check("fresh merge is not stale", not _merge_is_stale(91001, merged_abs))

# Regenerate the image after the merge was produced.
time.sleep(0.05)
future = time.time() + 5
os.utime(image_abs, (future, future))
check("merge is stale once the image is newer", _merge_is_stale(91001, merged_abs))
check("missing merged file counts as stale",
      _merge_is_stale(91001, os.path.join(os.path.dirname(merged_abs), "nope.mp4")))

for path in (image_abs, audio_abs, merged_abs):
    if os.path.exists(path):
        os.remove(path)

print("\n[BUG] Regenerating in a different format left the stale file winning lookup")
mp3_abs, _ = asset_service.write_asset_file("voiceover", 91002, "mp3", b"ID3" + b"a" * 64)
check("mp3 stored", os.path.exists(mp3_abs))
wav_abs, wav_url = asset_service.write_asset_file("voiceover", 91002, "wav", b"RIFF" + b"b" * 64)
check("old mp3 removed on format change", not os.path.exists(mp3_abs), mp3_abs)
found_audio = asset_service.stored_asset_path("voiceover", 91002, ("mp3", "wav"))
check("lookup resolves to the new wav", found_audio is not None and found_audio[1] == wav_url, found_audio)

# Re-writing the same format must not delete the file it just wrote.
same_abs, _ = asset_service.write_asset_file("voiceover", 91002, "wav", b"RIFF" + b"c" * 64)
check("re-write of same format survives", os.path.exists(same_abs), same_abs)

# A neighbouring scene id must not be swept up by the prefix match.
other_abs, _ = asset_service.write_asset_file("voiceover", 9100, "mp3", b"ID3" + b"d" * 64)
asset_service.write_asset_file("voiceover", 91003, "mp3", b"ID3" + b"e" * 64)
check("other scene's asset untouched", os.path.exists(other_abs), other_abs)

for path in [wav_abs, same_abs, other_abs]:
    if os.path.exists(path):
        os.remove(path)
leftover = asset_service.stored_asset_path("voiceover", 91003, ("mp3",))
if leftover:
    os.remove(leftover[0])

print("\n[BUG] Asset search broke PostgREST filters on , ( ) characters")
from backend.routers.assets_router import _safe_search

for hostile in ["a,b", "cat(dog)", 'quote"here', "back\\slash", "star*"]:
    cleaned = _safe_search(hostile)
    check(f"sanitized {hostile!r}", not any(ch in cleaned for ch in ',()"\\*'), cleaned)
check("ordinary text survives", _safe_search("sunset over rome") == "sunset over rome")
check("empty search stays empty", _safe_search("") == "")

print("\n[BUG] Non-numeric project_id raised a 500")
from fastapi import HTTPException

from backend.routers.files_router import _as_project_id

check("numeric string accepted", _as_project_id("12") == 12)
for bad in ["abc", None, "1; drop table", ""]:
    try:
        _as_project_id(bad)
        check(f"rejects {bad!r}", False, "no exception")
    except HTTPException as exc:
        check(f"rejects {bad!r} with 400", exc.status_code == 400, exc.status_code)

print("\n[BUG] Pause reason lost the underlying provider error")


async def scenario_pause_reason():
    pool = aps.ApiProfilePool([
        {"id": 1, "profile_name": "P1", "api_key": "k1", "priority": 0,
         "request_count": 0, "success_count": 0, "failure_count": 0, "unavailable_until_dt": None}
    ])
    profile = await pool.acquire()
    await pool.report_failure(profile, g.AuthError("Image generation: authentication failed — API key not valid"))
    try:
        await pool.acquire()
        return None
    except aps.NoAvailableProfileError as exc:
        return exc


error = asyncio.run(scenario_pause_reason())
check("pause error mentions the real cause", "API key not valid" in str(error), str(error))
check("pause error carries the exception", isinstance(error.cause, g.AuthError), error.cause)

print("\n[BUG] Supabase client rebuilt on every request")
from backend import database

if database.settings.supabase_url and database.settings.supabase_anon_key:
    first = database.get_db_client("token-aaa")
    second = database.get_db_client("token-aaa")
    other = database.get_db_client("token-bbb")
    check("same token reuses one client", first is second)
    check("different tokens never share a client", first is not other)

    for index in range(database._USER_CLIENT_CACHE_SIZE + 10):
        database.get_db_client(f"token-{index}")
    check("cache stays bounded",
          len(database._user_clients) <= database._USER_CLIENT_CACHE_SIZE,
          len(database._user_clients))
    database._user_clients.clear()
else:
    print("  SKIP  Supabase not configured")

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
