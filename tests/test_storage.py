"""Storage, asset verification and export packaging checks (real files, no network/db)."""
import asyncio
import os
import sys
import zipfile

sys.path.insert(0, r"e:\video automation\ai_batch_studio")

from backend.config import settings
from backend.services import asset_service, export_service

failures = []


def check(label, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + ("" if condition else f"  {detail}"))
    if not condition:
        failures.append(label)


print("\n[1] Writing assets to media storage")
image_abs, image_url = asset_service.write_asset_file("image", 90001, "png", b"\x89PNG\r\n\x1a\n" + b"0" * 512)
audio_abs, audio_url = asset_service.write_asset_file("voiceover", 90001, "mp3", b"ID3" + b"1" * 256)
check("image written", os.path.exists(image_abs) and os.path.getsize(image_abs) > 0, image_abs)
check("image url under images/", image_url.startswith("/output/images/90001_"), image_url)
check("audio url under audio/", audio_url.startswith("/output/audio/90001_"), audio_url)

print("\n[1b] Media URLs are not enumerable (§46, §52)")
check("image url is not the bare scene id", image_url != "/output/images/90001.png", image_url)
token = asset_service.storage_token("image", 90001)
check("token is long enough to resist guessing", len(token) >= 16, token)
check("token is stable across calls", asset_service.storage_token("image", 90001) == token)
check("token differs per asset type",
      asset_service.storage_token("voiceover", 90001) != token)
check("token differs per scene",
      asset_service.storage_token("image", 90002) != token)
# An attacker who knows a neighbouring scene id must not be able to derive this one.
neighbours = {asset_service.storage_token("image", i) for i in (90000, 90002, 90003)}
check("neighbouring scene ids give unrelated tokens", token not in neighbours)

try:
    asset_service.write_asset_file("image", 90002, "png", b"")
    check("empty asset rejected", False, "no exception raised")
except ValueError:
    check("empty asset rejected", True)

print("\n[2] Idempotency lookup")
found = asset_service.stored_asset_path("image", 90001, ("png", "jpg"))
check("existing asset found", found is not None and found[1] == image_url, found)
check("missing asset not found", asset_service.stored_asset_path("video", 90001, ("mp4",)) is None)

# Media generated before the tokenized naming must still resolve.
legacy_path = os.path.join(settings.videos_dir, "90001.mp4")
with open(legacy_path, "wb") as fh:
    fh.write(b"legacy-video-bytes")
legacy_found = asset_service.stored_asset_path("video", 90001, ("mp4",))
check("legacy un-tokenized file still found",
      legacy_found is not None and legacy_found[1] == "/output/videos/90001.mp4", legacy_found)
os.remove(legacy_path)

print("\n[3] Asset verification refuses unverifiable output")
check("missing file is not registered",
      asset_service.register_asset("u", 1, 90003, "image",
                                   os.path.join(settings.images_dir, "does-not-exist.png"),
                                   "/output/images/does-not-exist.png") is None)

empty_path = os.path.join(settings.images_dir, "90004.png")
open(empty_path, "wb").close()
check("empty file is not registered",
      asset_service.register_asset("u", 1, 90004, "image", empty_path, "/output/images/90004.png") is None)
os.remove(empty_path)

print("\n[4] URL -> local path mapping and traversal guard")
check("maps an image url back to disk",
      asset_service.local_path_for_url(image_url) == os.path.normpath(image_abs),
      asset_service.local_path_for_url(image_url))
check("maps without leading slash",
      asset_service.local_path_for_url(audio_url.lstrip("/")) == os.path.normpath(audio_abs),
      asset_service.local_path_for_url(audio_url.lstrip("/")))
for hostile in ["/output/../../.env", "../../.env", "/output/../../../etc/passwd"]:
    check(f"blocks traversal {hostile}", asset_service.local_path_for_url(hostile) is None,
          asset_service.local_path_for_url(hostile))
check("empty url handled", asset_service.local_path_for_url("") is None)

print("\n[5] Export packaging")
assets = [
    {"storage_path": image_url, "asset_type": "image", "filename": "scene_001.png",
     "scene_id": 90001, "scene_number": "1", "size": os.path.getsize(image_abs), "prompt": "a prompt"},
    {"storage_path": audio_url, "asset_type": "voiceover", "filename": "scene_001_voiceover.mp3",
     "scene_id": 90001, "scene_number": "1", "size": os.path.getsize(audio_abs), "prompt": ""},
    {"storage_path": "/output/videos/does-not-exist.mp4", "asset_type": "video",
     "filename": "missing.mp4", "scene_id": 90009, "scene_number": "9", "size": 0},
]
scenes = [{"scene_number": "1", "overall_status": "COMPLETED", "visual_path": image_url,
           "audio_path": audio_url, "media_type": "image_voice", "visual_prompt": "a prompt"}]

result = asyncio.run(export_service.create_export_zip("My Project!", assets, scenes, "type"))
zip_path = os.path.join(settings.output_dir, result["filename"])
check("zip created", os.path.exists(zip_path), zip_path)
check("2 assets included", result["asset_count"] == 2, result["asset_count"])
check("missing file reported", result["missing_count"] == 1, result["missing_count"])

with zipfile.ZipFile(zip_path) as archive:
    names = archive.namelist()
    check("images folder", any(n.endswith("/images/scene_001.png") for n in names), names)
    check("voiceovers folder", any(n.endswith("/voiceovers/scene_001_voiceover.mp3") for n in names), names)
    check("manifest present", any(n.endswith("metadata/manifest.json") for n in names), names)
    check("scenes csv present", any(n.endswith("metadata/scenes.csv") for n in names), names)
    check("project name sanitized", all(not n.startswith("My Project!") for n in names), names[:3])
    manifest = archive.read([n for n in names if n.endswith("manifest.json")][0]).decode()
    check("manifest records missing file", "does-not-exist.mp4" in manifest)
    csv_text = archive.read([n for n in names if n.endswith("scenes.csv")][0]).decode()
    check("scenes csv has header", csv_text.startswith("scene_number,media_type"), csv_text[:60])

scene_result = asyncio.run(export_service.create_export_zip("P", assets, scenes, "scene"))
with zipfile.ZipFile(os.path.join(settings.output_dir, scene_result["filename"])) as archive:
    check("scene-organized layout",
          any("/scene_1/" in n for n in archive.namelist()), archive.namelist())

print("\n[6] Report CSV")
report_url = asyncio.run(export_service.create_generation_report_csv("My Project!", scenes))
report_path = os.path.join(settings.output_dir, os.path.basename(report_url))
check("report written", os.path.exists(report_path), report_path)
with open(report_path, encoding="utf-8") as fh:
    content = fh.read()
check("report contains the scene", "COMPLETED" in content and "a prompt" in content, content[:120])

print("\n[7] Cleanup of test artifacts")
for path in [image_abs, audio_abs, zip_path,
             os.path.join(settings.output_dir, scene_result["filename"]), report_path]:
    if os.path.exists(path):
        os.remove(path)
check("test artifacts removed", not os.path.exists(image_abs) and not os.path.exists(zip_path))

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
