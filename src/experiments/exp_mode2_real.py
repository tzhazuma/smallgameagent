"""Mode 2 VLM inference with real game screenshot on ssh5090."""
import os
import sys
import json
import time
os.environ["HF_HUB_OFFLINE"] = "1"
sys.path.insert(0, ".")

from src.inference.server import GameAgentInference
from PIL import Image

engine = GameAgentInference(
    model_path="google/gemma-4-e4b-it",
    adapter_path="/home/tangzh/checkpoints/gemma4-e4b-all7/checkpoint-1000",
    use_flash_attn=False,
)

img = Image.open("/tmp/game_screenshot.png").convert("RGB")
print(f"Image size: {img.size}", flush=True)

with open("/tmp/game_state.json") as f:
    state = json.load(f)
print(f"State: ready={state.get('ready')} done={state.get('done')}", flush=True)

t0 = time.time()
result = engine.predict(img, state)
t = time.time() - t0
print(f"Inference time: {t:.1f}s", flush=True)
print(f"Action: {result.get('action')}", flush=True)
print(f"Params: {result.get('params')}", flush=True)
print(f"Reason: {result.get('reason', '')[:200]}", flush=True)

# Save result
json.dump(result, open("/tmp/exp_mode2_real.json", "w"), indent=2, ensure_ascii=False)
