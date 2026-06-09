# SmallGameAgent

LLM-driven agent that plays Cocos Creator HTML5 playable ads, plus a VLM fine-tuning pipeline to replace the LLM with a specialized vision-language model.

Two phases:
- **Phase 1 (Agent)**: DeepSeek-v4-flash reasons over game state via a JavaScript probe. Mimo-v2.5 analyzes screenshots for visual cues. The agent dispatches CDP touch events (joystick, tap) to control the game through Playwright.
- **Phase 2 (Training)**: QLoRA fine-tune a VLM (Qwen3.5-4B or Gemma-4-E4B) on ~9K human-demonstrated gameplay samples. The trained adapter replaces the text LLM for lower latency, offline inference, and better game-specific performance.

> **⚠️ Gemma-4-E4B status**: Training is currently blocked by a transformers 5.9.0 multimodal projection bug (`Image features and image tokens do not match`). This requires either upgrading transformers past 5.9.0 or direct patching of `Gemma4MultiModalProjector`. The Qwen3.5-4B pipeline is fully operational — see [training results](src/docs/report.pdf).

## Quickstart

```bash
# 1. Install dependencies
pip install -e .

# 2. Set your API key
export OPENCODE_API_KEY="sk-..."

# 3. Run the agent on a game
python -c "
import asyncio
from src.agent import LLMAgent, OpenCodeGoClient
client = OpenCodeGoClient()
agent = LLMAgent(client)
result = asyncio.run(agent.run_game('playable-agent-12-games-20260608/playables/SSD_00848P01_*.html'))
print(result)
"
```

## Installation

### Basic (agent only)

```bash
pip install -e .
```

Requires Python 3.14+, Playwright with Chromium:

```bash
playwright install chromium
```

### Full (agent + training)

```bash
pip install -e .[training]
```

Adds PyTorch 2.5+, Transformers 4.50+, PEFT, TRL, bitsandbytes, Accelerate, Datasets, and WandB. See `pyproject.toml` for the exact dependency tree.

### Development

```bash
pip install -e .[dev]
```

Adds pytest 8+, pytest-asyncio, and ruff 0.10+.

## Configuration

### API Key

The agent needs an OpenCodeGo API key for LLM inference.

| Method | Example |
|--------|---------|
| Environment variable | `export OPENCODE_API_KEY="sk-..."` |
| Explicit in code | `OpenCodeGoClient(api_key="sk-...")` |

Default endpoint: `https://opencode.ai/zen/go/v1`

### Game Profiles

Per-game joystick anchors, calibration matrices, and driver types live in `configs/game_profiles.py`. The agent uses the profile matching the loaded game HTML to resolve joystick anchor points and movement vectors.

### Agent Configuration

Pass a config dict to `LLMAgent`:

```python
config = {
    "text_model": "deepseek-v4-flash",       # default
    "vision_model": "mimo-v2.5",             # default
    "max_steps": 200,
    "probe_timeout_ms": 18_000,
    "collect_dataset": True,                 # emits dataset from gameplay
}
```

## Project Structure

```
delivery/
├── pyproject.toml                  # Build config, deps, tool settings
├── configs/
│   └── game_profiles.py            # 12 game profiles with joystick/calibration data
├── scripts/
│   ├── scp_to_ssh5090.sh           # Push code to GPU server
│   ├── scp_training_data.sh        # Push training data to data server
│   └── sync_from_ssh5092.sh        # Pull results back
├── src/
│   ├── agent/
│   │   ├── api_client.py           # OpenAI-compatible client for OpenCodeGo
│   │   ├── harness.py              # Playwright browser harness (CDP touch)
│   │   ├── llm_agent.py            # Core observe-think-act loop
│   │   ├── probe_adapter.py        # Cocos Creator JS probe bridge
│   │   └── visual_analyzer.py      # Mimo-v2.5 + PIL fallback analysis
│   ├── training/
│   │   ├── data_loader.py          # VLMColdStartDataset (lazy JSONL loader)
│   │   ├── dataset_converter.py    # JSONL → HF Dataset / ShareGPT
│   │   ├── train_qwen35.py         # Qwen3.5-4B QLoRA training script
│   │   └── train_gemma4.py         # Gemma-4-E4B QLoRA training script
│   ├── inference/                  # Inference server (coming soon)
│   └── docs/
│       ├── architecture.md         # System architecture documentation
│       └── training_guide.md       # GPU training instructions
├── tests/
│   ├── test_agent_core.py          # 56 tests - LLMAgent logic
│   ├── test_api_client.py          # 15 tests - API client
│   ├── test_data_loader.py         # 22 tests - dataset loading
│   ├── test_dataset_converter.py   # 40 tests - format conversion
│   ├── test_game_configs.py        # 16 tests - game profiles
│   ├── test_harness.py             # 22 tests - GameRunner
│   ├── test_probe_adapter.py       # 30 tests - ProbeAdapter
│   └── test_visual_analyzer.py     # 44 tests - VisualAnalyzer
│                                 # (245 total)
├── playable-agent-12-games-20260608/   # Game HTMLs (gitignored)
└── vlm-training-data-cold-start-portable-20260608/  # Training data (gitignored)
```

## Datasets

The VLM cold-start dataset contains 9,378 training samples across 7 task types, structured as JSONL with referenced image assets:

| Task | Train | Val | Description |
|------|-------|-----|-------------|
| next_probe_action | 1,647 | 147 | Choose the next probe action |
| probe_action_effect | 1,615 | 179 | Predict effect of a probe action |
| field_grounding | 2,221 | 243 | Ground spatial observations |
| information_gain_judgment | 1,637 | 157 | Judge information gain |
| pulse_response_grounding | 1,199 | 118 | Ground joystick pulse response |
| progression_grounding | 987 | 103 | Detect game progression |
| failure_recovery | 72 | 11 | Recover from failure states |

## Tests

```bash
pytest              # 245 tests across 8 test files
pytest -v           # verbose
pytest tests/test_agent_core.py  # specific module
```

Uses pytest-asyncio (auto mode) for async test support. Mocked API calls and browser - no live connection needed.

## Documentation

- [Architecture](src/docs/architecture.md) - system design, data flow, component details
- [Training Guide](src/docs/training_guide.md) - GPU training setup on ssh5090
- [Strategy Audit](src/docs/strategy_audit.md) - game driver strategy analysis
