"""Offline checks for the pure pipeline logic (no network, no database)."""
import io
import os
import sys

sys.path.insert(0, r"e:\video automation\ai_batch_studio")

from backend.services import csv_service, prompt_service, task_service
from backend.services.generation_service import _scene_status_from_tasks

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


print("\n[1] Real sample.csv analysis")
with open(r"e:\video automation\ai_batch_studio\sample.csv", "rb") as fh:
    result = csv_service.analyze(fh.read(), "sample.csv")

check("delimiter detected", result["delimiter"] == ",", result["delimiter"])
check("all rows valid", result["invalid_count"] == 0, result["invalid_count"])
check("rows parsed", result["total_rows"] >= 3, result["total_rows"])
mapping = {c["original_name"]: c["detected_meaning"] for c in result["detected_columns"]}
check("id -> scene_number", mapping["id"] == "scene_number", mapping)
check("visual_prompt mapped", mapping["visual_prompt"] == "visual_prompt", mapping)
check("voiceover mapped", mapping["voiceover_script"] == "voiceover_script", mapping)
check("filename mapped", mapping["filename"] == "filename", mapping)
first = result["valid_rows"][0]
check("media type inferred", first["media_type"] == "image_voice", first["media_type"])
check("aspect kept", first["aspect_ratio"] == "16:9", first["aspect_ratio"])
check("media requirements", result["detected_media_requirements"] == {
    "has_images": True, "has_voiceovers": True, "has_videos": False},
    result["detected_media_requirements"])

print("\n[2] Messy CSV: semicolons, odd headers, unknown columns, BOM")
messy = (
    "\ufeffScene No;Image Description;Narration Text;Ratio;Mood;Camera Angle;Historical Period\r\n"
    "1;A vast desert at dawn with towering dunes and long shadows;The sands remember every "
    "footstep that crossed them.;9:16;Serene;Low angle wide shot;Bronze Age\r\n"
    "2;A lone lighthouse against a violent storm, waves crashing over rocks;Some lights refuse "
    "to go out.;9:16;Tense;Aerial;Modern\r\n"
).encode("utf-8")
messy_result = csv_service.analyze(messy, "messy.csv")

messy_map = {c["original_name"]: c["detected_meaning"] for c in messy_result["detected_columns"]}
check("semicolon delimiter", messy_result["delimiter"] == ";", messy_result["delimiter"])
check("BOM handled", "Scene No" in messy_map, list(messy_map))
check("'Image Description' -> visual_prompt", messy_map["Image Description"] == "visual_prompt", messy_map)
check("'Narration Text' -> voiceover_script", messy_map["Narration Text"] == "voiceover_script", messy_map)
check("'Ratio' -> aspect_ratio", messy_map["Ratio"] == "aspect_ratio", messy_map)
check("'Camera Angle' -> camera", messy_map["Camera Angle"] == "camera", messy_map)
check("unknown column preserved", messy_map["Historical Period"] == "custom_metadata", messy_map)

scene = messy_result["valid_rows"][0]
check("custom metadata retained",
      scene["custom_metadata"].get("Historical Period") == "Bronze Age", scene["custom_metadata"])
check("camera folded into metadata", scene["custom_metadata"].get("camera") == "Low angle wide shot",
      scene["custom_metadata"])
check("9:16 preserved", scene["aspect_ratio"] == "9:16", scene["aspect_ratio"])

print("\n[3] Manual mapping override")
override = dict(messy_map)
override["Mood"] = "tone"
override["Historical Period"] = "ignore"
remapped = csv_service.normalize_rows(messy_result["raw_rows"], override)
row = remapped["valid_rows"][0]
check("tone applied from override", row.get("tone") == "Serene", row.get("tone"))
check("ignored column dropped", "Historical Period" not in row["custom_metadata"], row["custom_metadata"])

print("\n[4] Arbitrary CSV with no recognisable prompt column")
odd = (
    "reference,notes\n"
    "A-100,\"A neon-drenched alley in a rain-soaked megacity, steam rising from grates, "
    "a lone figure under a flickering sign\"\n"
).encode("utf-8")
odd_result = csv_service.analyze(odd, "odd.csv")
check("fallback produced a scene", odd_result["valid_count"] == 1, odd_result["invalid_count"])
check("longest text became visual prompt",
      odd_result["valid_rows"][0]["visual_prompt"].startswith("A neon-drenched"),
      odd_result["valid_rows"][0].get("visual_prompt"))

print("\n[5] Prompt composition")
rich = {
    "master_prompt": "Cinematic documentary, 35mm film",
    "visual_prompt": "A blacksmith hammering glowing steel",
    "style": "Chiaroscuro",
    "tone": "Reverent",
    "negative_prompt": "blurry, watermark",
    "custom_metadata": {"character": "Old craftsman", "lighting": "Forge firelight",
                        "location": "Stone workshop", "unrelated_column": "ignore me"},
}
composed = prompt_service.compose_image_prompt(rich)
for fragment in ["Cinematic documentary", "blacksmith", "Style: Chiaroscuro", "Tone: Reverent",
                 "Character: Old craftsman", "Lighting: Forge firelight", "Location: Stone workshop"]:
    check(f"prompt contains '{fragment}'", fragment in composed, composed)
check("irrelevant metadata excluded", "ignore me" not in composed, composed)
check("negative kept separate", "watermark" not in composed, composed)
check("negative prompt readable", prompt_service.negative_prompt_for(rich) == "blurry, watermark")

sparse = {"visual_prompt": "A single red balloon"}
check("sparse prompt is just the prompt",
      prompt_service.compose_image_prompt(sparse) == "A single red balloon",
      prompt_service.compose_image_prompt(sparse))

script, options = prompt_service.compose_voice_request(
    {"voiceover_script": "Hello world.", "tone": "Warm", "voice_name": "en-US-Neural2-F",
     "language": "en-US", "speaking_speed": 1.15}, {})
check("tone folded into script", script.startswith("Say in a warm tone:"), script)
check("voice passed through", options["voice"] == "en-US-Neural2-F", options)
check("speed passed through", abs(options["speed"] - 1.15) < 1e-6, options)

print("\n[6] Task planning (media type detection)")
cases = [
    ({"media_type": "image", "visual_prompt": "x"}, {"image"}),
    ({"media_type": "voice", "voiceover_script": "x"}, {"voiceover"}),
    ({"media_type": "image_voice", "visual_prompt": "x", "voiceover_script": "y"},
     {"image", "voiceover", "merge"}),
    ({"media_type": "video_voice", "video_prompt": "x", "voiceover_script": "y"},
     {"video", "voiceover"}),
    ({"visual_prompt": "x"}, {"image"}),                      # inferred
    ({"voiceover_script": "x"}, {"voiceover"}),               # inferred
    ({"visual_prompt": "x", "voiceover_script": "y"}, {"image", "voiceover", "merge"}),
    ({"media_type": "image"}, set()),                          # nothing to generate
]
for scene_case, expected in cases:
    planned = {t["task_type"] for t in task_service.plan_tasks(scene_case, {"merge_enabled": True})}
    check(f"plan {scene_case.get('media_type') or 'inferred'} -> {sorted(expected) or 'none'}",
          planned == expected, planned)

no_merge = {t["task_type"] for t in task_service.plan_tasks(
    {"visual_prompt": "x", "voiceover_script": "y"}, {"merge_enabled": False})}
check("merge disabled respected", no_merge == {"image", "voiceover"}, no_merge)

print("\n[7] Scene status derived from tasks (blue-check integrity)")
S = task_service
scenarios = [
    ("all completed", [{"task_type": "image", "status": S.STATUS_COMPLETED},
                       {"task_type": "voiceover", "status": S.STATUS_COMPLETED}], "COMPLETED"),
    ("one failed", [{"task_type": "image", "status": S.STATUS_COMPLETED},
                    {"task_type": "voiceover", "status": S.STATUS_FAILED,
                     "error_message": "quota"}], "FAILED"),
    ("still running", [{"task_type": "image", "status": S.STATUS_PROCESSING}], "PROCESSING"),
    ("queued", [{"task_type": "image", "status": S.STATUS_QUEUED}], "PENDING"),
    ("all unsupported", [{"task_type": "video", "status": S.STATUS_UNSUPPORTED}], "SKIPPED"),
    ("no tasks", [], "PENDING"),
]
for label, tasks, expected in scenarios:
    state = _scene_status_from_tasks(tasks)
    check(f"scene status: {label} -> {expected}", state["overall_status"] == expected, state["overall_status"])

partial = _scene_status_from_tasks([
    {"task_type": "image", "status": S.STATUS_COMPLETED},
    {"task_type": "voiceover", "status": S.STATUS_PROCESSING},
])
check("image marked complete only when its task completed",
      partial["visual_status"] == "VISUAL_COMPLETED", partial)
check("voice not marked complete while generating",
      partial["voice_status"] == "VOICE_GENERATING", partial)

never_ran = _scene_status_from_tasks([{"task_type": "image", "status": S.STATUS_QUEUED}])
check("queued image is not reported complete", never_ran["visual_status"] == "PENDING", never_ran)

print("\n[8] Progress percentages")
class FakeQuery:
    def __init__(self, rows): self.rows = rows
    def select(self, *_a, **_k): return self
    def eq(self, *_a): return self
    def execute(self): return type("R", (), {"data": self.rows})()

class FakeClient:
    def __init__(self, rows): self.rows = rows
    def table(self, _name): return FakeQuery(self.rows)

rows = [
    {"id": 1, "task_type": "image", "status": "COMPLETED", "scene_id": 1, "scene_number": "1"},
    {"id": 2, "task_type": "voiceover", "status": "COMPLETED", "scene_id": 1, "scene_number": "1"},
    {"id": 3, "task_type": "image", "status": "FAILED", "scene_id": 2, "scene_number": "2",
     "error_message": "429"},
    {"id": 4, "task_type": "image", "status": "PROCESSING", "scene_id": 3, "scene_number": "3"},
]
progress = task_service.compute_progress(FakeClient(rows), 1)
check("total counted", progress["overall"]["total"] == 4, progress["overall"])
check("completed counted", progress["overall"]["completed"] == 2, progress["overall"])
check("percent from finished work", progress["percent"] == 50, progress["percent"])
check("images bucket", progress["images"] == {"total": 3, "completed": 1, "failed": 1,
                                              "processing": 1, "pending": 0, "cancelled": 0,
                                              "unsupported": 0, "skipped": 0}, progress["images"])
check("currently processing surfaced", progress["currently_processing"][0]["scene_number"] == "3",
      progress["currently_processing"])
check("failure surfaced", progress["recent_failures"][0]["error"] == "429", progress["recent_failures"])

print("\n[9] Key masking never leaks the secret")
from backend.services.api_profile_service import mask_key
secret = "AIzaSyD-EXAMPLEKEY-1234567890abcdef"
masked = mask_key(secret)
check("masked ends with last 4", masked.endswith(secret[-4:]), masked)
check("masked hides the rest", secret[:20] not in masked, masked)

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
