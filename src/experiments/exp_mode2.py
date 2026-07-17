"""Mode 2 VLM inference experiment on ssh5090."""
import os
import sys
import json
import time
os.environ["HF_HUB_OFFLINE"] = "1"
sys.path.insert(0, ".")

from src.inference.server import GameAgentInference
from PIL import Image

games = [
    ("SSD_00848P01", "传送带种地"),
    ("SSD_00853P01", "货车很急"),
    ("SSD_00862P01", "切木鱼"),
]

t0 = time.time()
engine = GameAgentInference(
    model_path="google/gemma-4-e4b-it",
    adapter_path="/home/tangzh/checkpoints/gemma4-e4b-all7/checkpoint-1000",
    use_flash_attn=False,
)
load_time = time.time() - t0
print(f"Load time: {load_time:.1f}s", flush=True)

results = []
for gid, label in games:
    img = Image.new("RGB", (750, 1334), (120, 180, 200))
    state = {"ready": True, "done": False, "player": {"screenPosition": {"x": 375, "y": 667}}}
    t0 = time.time()
    try:
        result = engine.predict(img, state)
    except Exception as e:
        result = {"action": "error", "params": {}, "reason": str(e)[:200]}
    infer_time = time.time() - t0
    result["game_id"] = gid
    result["label"] = label
    result["inference_time_s"] = round(infer_time, 1)
    results.append(result)
    print(f"{gid} ({label}): action={result.get('action')} time={infer_time:.1f}s reason={result.get('reason','')[:80]}", flush=True)

json.dump(results, open("/tmp/experiment_mode2.json", "w"), indent=2, ensure_ascii=False)
print(f"Saved {len(results)} results", flush=True)
