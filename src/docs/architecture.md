# Architecture

## System Overview

SmallGameAgent has two phases. Phase 1 is an online LLM-driven agent that plays Cocos Creator HTML5 games. Phase 2 is a training pipeline that fine-tunes a VLM to eventually replace the LLM component.

```
Phase 1 ──────────────────────────────────────────────────────────────┐
                                                                       │
  ┌──────────┐    ┌──────────────┐    ┌────────────┐                  │
  │  Cocos   │    │  Playwright  │    │  Agent     │                  │
  │  Game    │◄──►│  Harness     │◄──►│  Loop      │                  │
  │  (HTML)  │    │  (CDP/Page)  │    │  (LLMAgent)│                  │
  └──────────┘    └──────┬───────┘    └─────┬──────┘                  │
       │                  │                   │                        │
       │ injects          │ screenshots      │ text + vision queries  │
       ▼                  ▼                   ▼                        │
  ┌──────────┐    ┌──────────────┐    ┌──────────────┐               │
  │  JS      │    │  Visual      │    │  OpenCodeGo  │               │
  │  Probe   │    │  Analyzer    │    │  API Client  │               │
  │(Cocos    │    │(Mimo-v2.5)   │    │(DeepSeek-v4) │               │
  │  Runtime)│    │ + PIL fallback│   │              │               │
  └──────────┘    └──────────────┘    └──────────────┘               │
       │                                                              │
       ▼                                                              │
  ┌──────────┐    Dataset collection enabled?                        │
  │  Game    │    ──────────────────────                              │
  │  Profile │    yes → emit JSONL samples                           │
  │  Config  │                                                        │
  └──────────┘                                                        │
                                                                       │
Phase 2 ──── (uses collected dataset for training) ──────────────────┘
                                                                      
  ┌──────────────────┐     ┌─────────────────┐     ┌───────────────┐
  │  JSONL Dataset   │────►│ DatasetLoader   │────►│ QLoRA Trainer │
  │  (9K samples)    │     │ + Converter     │     │ (PEFT/TRL)    │
  └──────────────────┘     └─────────────────┘     └───────┬───────┘
                                                            │
                                              ┌─────────────▼──────────┐
                                              │  Trained LoRA Adapter │
                                              │  (Qwen3.5-4B /        │
                                              │   Gemma-4-E4B)        │
                                              └───────────────────────┘
```

## Data Flow

### Observe-Think-Act Loop (Phase 1)

```
                     ┌─────────────────────────────┐
                     │         LLMAgent             │
                     │   .run_game(game_path)       │
                     └──────────┬──────────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
          ▼                     ▼                     ▼
   ┌────────────┐     ┌────────────────┐     ┌──────────────┐
   │  OBSERVE   │     │    THINK       │     │    ACT       │
   │            │     │                │     │              │
   │ 1. Launch  │     │ 1. Build text  │     │ 1. Dispatch  │
   │    browser │     │    prompt      │     │    CDP touch │
   │ 2. Inject  │     │    (state +    │     │    events    │
   │    JS probe│     │     history)   │     │ 2. Joystick  │
   │ 3. Poll    │     │ 2. DeepSeek    │     │    pulse     │
   │    state   │     │    → action    │     │ 3. Tap       │
   │    (ready, │     │ 3. Mimo-v2.5   │     │ 4. Wait      │
   │     win,   │     │    → visual    │     │              │
   │     done)  │     │ 4. Fuse text + │     │              │
   │ 4. Capture │     │    vision      │     │              │
   │    screen- │     │ 5. Parse JSON  │     │              │
   │    shot    │     │    action      │     │              │
   └────────────┘     └────────────────┘     └──────────────┘
```

**Step details:**

1. **Observe**: GameRunner launches Chromium in an iPhone viewport (375x812, 3x DPR), opens the game HTML, injects the Cocos Creator probe (`ProbeAdapter.inject`), and polls `probe.observe()` until `ready` is true. The probe returns structured state: player position, key numbers, flags, guide candidates, and chain data.

2. **Think-Text**: The text prompt embeds the current state JSON plus the last 5 action-history entries. The LLM (DeepSeek-v4-flash) returns a JSON action: `{"action": "move"|"tap"|"wait", "params": {...}, "reason": "..."}`. JSON parsing retries up to 2 times with correction hints, then falls back to `wait`.

3. **Think-Vision**: The current screenshot is sent to Mimo-v2.5 for visual analysis (arrows, end cards, obstacles, UI buttons). The vision response can override the text decision: end screens force `wait`, detected arrows override joystick direction.

4. **Fuse**: `_fuse_decisions` merges text and vision outputs. Vision overrides take priority for end screens and arrow directions. Otherwise the text model's action is used.

5. **Act**: The action dispatches CDP touch events:
   - `move` - touchStart at joystick anchor, touchMove to `anchor + dx*radius, dy*radius`, hold, touchEnd
   - `tap` - single tap at screen coordinate via CDP
   - `wait` - `page.wait_for_timeout`

6. **Record**: The step's state summary, decision, and screenshot are appended to the history window (last 20 steps). If `collect_dataset` is enabled, the full observation-action pair is recorded for training.

### Dataset Flow (Phase 1 → Phase 2)

```
Agent collects data ──► JSONL samples written to disk
                                │
                                ▼
                      VLMColdStartDataset.load()
                                │
                                ▼
                      VLMDatasetConverter
                        ├── to_qwen35_messages()
                        ├── to_gemma4_messages()
                        ├── to_hf_dataset()
                        └── to_sharegpt_format()
                                │
                                ▼
                      QLoRA Training (train_qwen35.py / train_gemma4.py)
                                │
                                ▼
                      LoRA adapter (saved to checkpoints/)
```

The converter transforms raw JSONL state snapshots into model-specific chat templates. Each sample becomes a multimodal conversation: system prompt, user message (images + state summary), and assistant message (target action). The HuggingFace Datasets format feeds directly into TRL's SFTTrainer.

## Component Breakdown

### src/agent/ - Phase 1 Agent

| Component | File | Role |
|-----------|------|------|
| OpenCodeGoClient | `api_client.py` | OpenAI-compatible HTTP client. Wraps the `openai` SDK with retry (429/503), exponential backoff, and image-to-base64 encoding. Two call surfaces: `chat()` for text models, `chat_with_vision()` for multimodal models. |
| LLMAgent | `llm_agent.py` | Central observe-think-act loop. Manages state history, prompt building, JSON parsing (with markdown-fence stripping), text/vision fusion, action dispatch, and optional dataset collection. Configurable via dict overrides. |
| GameRunner | `harness.py` | Playwright async context manager. Launches Chromium with iPhone viewport, opens game HTML via file URI, manages CDP session for low-level touch dispatch. Methods: `joystick_pulse`, `tap`, `screenshot`, `wait`. |
| ProbeAdapter | `probe_adapter.py` | Python bridge to the Cocos Creator probe JavaScript. Extracts the IIFE from the ESM source file, injects it via `add_init_script` + `evaluate`, wraps probe methods: `observe`, `observeFast`, `moveByCocosInput`, `getGuideSummary`, `snapshotComponents`. |
| VisualAnalyzer | `visual_analyzer.py` | Dual-path screenshot analysis. Primary path calls Mimo-v2.5 with a structured JSON prompt. Fallback path runs PIL colour-thresholding: cyan guide detection (b>=135, g>=105, r<=130), green/red/blue end-card detection, dark-obstacle connected components, bright UI button regions, and gold coin detection. |

### src/training/ - Phase 2 Training

| Component | File | Role |
|-----------|------|------|
| VLMColdStartDataset | `data_loader.py` | Lazy JSONL dataset loader. Loads line offsets on init, parses on `__getitem__`, caches parsed structures, lazily loads PIL images from task-relative paths. Supports 7 task types and 4 splits (train, val, smoke, all). |
| VLMDatasetConverter | `dataset_converter.py` | Format converter. Converts raw samples to Qwen3.5-VL or Gemma-4 chat templates, HuggingFace Dataset objects, and ShareGPT-format JSON for LLaMA-Factory. Builds compact state summaries from input_raw. |
| train_qwen35.py | `train_qwen35.py` | QLoRA fine-tuning script for Qwen3.5-4B. Uses 4-bit NF4 quantization, LoRA (default r=16, alpha=32), DeepSpeed ZeRO-2, Flash Attention 2, and TRL SFTTrainer. Custom MultimodalDataCollator applies `apply_chat_template` and tokenises with image support. |
| train_gemma4.py | `train_gemma4.py` | QLoRA fine-tuning script for Gemma-4-E4B. Similar pipeline with model-specific differences: Gemma-4 LoRA targets include gate_proj/up_proj/down_proj, uses `AutoModelForVision2Seq`, and extends TRL's base `Trainer` (not SFTTrainer). |

### src/inference/ - Inference Server

Not yet implemented. The inference server will load a trained LoRA adapter and base model, expose a REST or gRPC endpoint that accepts game state + screenshot, and returns actions. The agent loop would replace the `OpenCodeGoClient.chat()` call with a local inference call.

### configs/ - Configuration

| File | Role |
|------|------|
| `game_profiles.py` | Dictionary of 12 game profiles. Each profile defines: joystick anchor (screen coordinates), joystick radius, calibration basis (screen→world mapping), ground arrival threshold, target dwell time, and driver type. Driver types include: `follow-guide-audited`, `2d-audited`, `learned`, `taskguide`, `target-arrow`, `guide-follow`. |

The agent resolves the current game's profile at runtime to determine joystick anchor points and world-coordinate mapping. Calibration bases define how screen-space drags translate to in-game movement vectors:

```python
"calibration": {
    "basis": {
        "screen_right": {"x": 2.1227, "z": 2.1227},
        "screen_down": {"x": -2.0652, "z": 2.0652},
    },
}
```

## Configuration System

The agent is configured through:

1. **Game profiles** (`configs/game_profiles.py`) - per-game calibration data, selected by game_id at runtime
2. **Agent config dict** - passed to `LLMAgent(config={...})` at construction time:

| Key | Default | Description |
|-----|---------|-------------|
| `text_model` | `deepseek-v4-flash` | Text reasoning model |
| `vision_model` | `mimo-v2.5` | Vision analysis model |
| `max_steps` | `200` | Max observation cycles |
| `probe_timeout_ms` | `18000` | Max wait for probe ready |
| `probe_retry_delay_ms` | `1000` | Delay between probe retries |
| `max_json_retries` | `2` | Retries on JSON parse failure |
| `step_cooldown_ms` | `50` | Cooldown between steps |
| `collect_dataset` | `False` | Enable dataset recording |

3. **Environment variables**:
   - `OPENCODE_API_KEY` - API key for OpenCodeGo inference

## Testing Strategy

All 245 tests use `unittest.mock` to isolate the component under test. No live API key, browser, or GPU is required.

| Test file | Tests | What it covers |
|-----------|-------|----------------|
| `test_agent_core.py` | 56 | LLMAgent: observe-think-act loop, prompt building, JSON parsing, decision fusion, retry logic, fallback behavior on API failure |
| `test_api_client.py` | 15 | OpenCodeGoClient: image encoding, retry on 429/503, missing API key error, chat with vision |
| `test_data_loader.py` | 22 | VLMColdStartDataset: lazy loading, __getitem__, cache behavior, image resolution, edge cases (empty lines, invalid JSON) |
| `test_dataset_converter.py` | 40 | VLMDatasetConverter: Qwen3.5/Gemma-4 message formats, ShareGPT export, image path resolution, HF Dataset conversion |
| `test_game_configs.py` | 16 | Game profiles: 12 profiles exist, validation of profile schema, get_profile/list_all_game_ids |
| `test_harness.py` | 22 | GameRunner: browser lifecycle, joystick_pulse CDP protocol, screenshot capture, open_game error handling |
| `test_probe_adapter.py` | 30 | ProbeAdapter: ESM extraction, inject, observe/observeFast, wait_for_ready timeout, moveByCocosInput |
| `test_visual_analyzer.py` | 44 | VisualAnalyzer: API analysis, PIL fallback, cyan guide detection, end-state detection, caching, JSON parsing |

Key testing patterns:
- Mock `OpenAI` constructor and `chat.completions.create` for API calls
- Mock `playwright.async_api` for browser operations
- Use `tmp_path` fixture for temp files and images
- Test retry exhaustion, fallback modes, and malformed responses
