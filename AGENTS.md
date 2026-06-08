<!-- Generated: 2026-06-08 | Updated: 2026-06-08 -->

# smallgameagent

## Purpose

LLM-driven game-playing agent for Cocos Creator HTML5 playable ads (Phase 1) and VLM fine-tuning pipeline (Phase 2). The Phase 1 agent uses DeepSeek-v4-flash for text reasoning, Mimo-v2.5 for vision analysis, and CDP touch events for game control via Playwright. Phase 2 produces a QLoRA-adapted VLM (Qwen3.5-4B or Gemma-4-E4B) trained on ~9K human-demonstrated gameplay samples across 7 task types.

## Key Files

| File | Description |
|------|-------------|
| `pyproject.toml` | Project metadata, dependencies, ruff/pytest config |
| `configs/game_profiles.py` | 12 game profiles with joystick anchors, calibration bases, driver types |
| `scripts/scp_to_ssh5090.sh` | Sync source code to the GPU compute server |
| `scripts/scp_training_data.sh` | Sync training data to the storage/data server |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `src/` | All source code (see `src/AGENTS.md`) |
| `src/agent/` | Phase 1: agent loop, API client, harness, probe, visual analyzer |
| `src/training/` | Phase 2: data loading, dataset conversion, QLoRA training scripts |
| `src/inference/` | Inference server (coming in parallel task) |
| `src/docs/` | Architecture and training documentation |
| `configs/` | Game profiles and configuration |
| `tests/` | 245 tests across 8 test files |
| `scripts/` | SCP/rsync transfer scripts for GPU servers |
| `playable-agent-12-games-20260608/` | Game HTMLs (gitignored, ~53K files) |
| `vlm-training-data-cold-start-portable-20260608/` | Training dataset (gitignored, ~9K samples) |

## For AI Agents

### Working In This Directory

- All Python code targets Python 3.14. Use `from __future__ import annotations` in every file.
- Use `ruff` for linting and formatting (line-length 100, target-version py314). Run `ruff check .` before committing.
- Async-first pattern. Use `asyncio` for I/O-bound operations (API calls, browser control). Sync code is OK for data processing.
- Tests use pytest with `asyncio_mode = "auto"`. Test functions can be `async def` and pytest runs them automatically.
- Mock all external dependencies in tests (API calls, browser, filesystem). No test should require a live API key or network.
- The `.gitignore` excludes game HTMLs, PNGs, model weights, checkpoints, and the large data directories. Do not commit these.
- Game files and training data live outside the repo on disk. Paths reference them relative to the project root.

### Common Tasks

- **Run agent on a game**: `python -c "import asyncio; from src.agent import LLMAgent, OpenCodeGoClient; agent = LLMAgent(OpenCodeGoClient()); print(asyncio.run(agent.run_game('path/to/game.html')))"`
- **Run tests**: `pytest` (all 245), `pytest tests/test_agent_core.py` (specific)
- **Lint**: `ruff check .`
- **Format**: `ruff format .`
- **Push code to GPU server**: `bash scripts/scp_to_ssh5090.sh`
- **Push training data**: `bash scripts/scp_training_data.sh`
- **Start training on ssh5090**: `ssh tangzh@10.19.138.148`, then `cd delivery && python src/training/train_qwen35.py --dataset-root ../data/vlm-training-data-cold-start-portable-20260608/`

### Testing Requirements

- All tests must pass before claiming completion of any change: `pytest`
- Use `unittest.mock` (MagicMock, patch) for all external interfaces.
- Write new tests alongside new code. Minimum: one test per public function/method.
- Test edge cases: empty state, JSON parse failures, API timeouts, missing files.

### Conventions

- **Python 3.14** minimum. Always use `from __future__ import annotations`.
- **ruff** for linting and formatting. Config in `pyproject.toml`: line-length 100.
- **Async I/O**: Use `async`/`await` for network, browser, and filesystem. Keep CPU-bound code synchronous.
- **Type annotations**: Full type hints on all function signatures. Use `TYPE_CHECKING` for type-only imports.
- **Naming**: `snake_case` for functions/variables, `PascalCase` for classes, `SCREAMING_SNAKE` for module-level constants.
- **Logging**: Use `logging.getLogger(__name__)` at module level, not `print()`.
- **Tests**: pytest with `asyncio_mode="auto"`. One fixture file per test module. `tmp_path` for temp files.
- **Imports**: Standard library first, third-party second, local src imports third. Use blank line separators.
- **Docstrings**: NumPy-style docstrings for public API. Brief comments for internals.
- **Error handling**: Prefer specific exception types. Use `raise` from `...` for chaining.

## Dependencies

### Internal

- `src/agent/` exports via `__init__.py`: `GameRunner`, `LLMAgent`, `OpenCodeGoClient`, `ProbeAdapter`, `VisualAnalyzer`, `find_game_html`
- `src/training/` depends on `src/agent/` only through dataset_converter importing from data_loader
- `configs/game_profiles.py` is a standalone data module consumed by the agent at runtime

### External

- `openai>=1.0` - API client for OpenCodeGo inference
- `playwright>=1.50` - Browser automation for game control
- `pillow>=10.0` - Screenshot analysis and image processing
- `numpy>=2.0` - Array operations in visual analyzer
- Training extras: `torch>=2.5`, `transformers>=4.50`, `peft>=0.15`, `trl>=0.15`, `accelerate>=1.0`, `bitsandbytes>=0.45`, `datasets>=3.0`, `wandb>=0.19`

## Environment

### Local Development

- Python 3.14 in a `.venv`
- `pip install -e .[dev]` for all dev dependencies
- Playwright Chromium: `playwright install chromium`
- No GPU required. Phase 1 agent uses API calls. Phase 2 training only on GPU servers.

### ssh5090 (10.19.138.148) - Compute Server

- User: `tangzh`, Password: `4dvlab123`
- 4x RTX 5090 32 GB, Python 3.14.5
- Full ML stack pre-installed (PyTorch, CUDA, Transformers, etc.)
- Code synced via `scripts/scp_to_ssh5090.sh` to `/home/tangzh/delivery/`
- Dataset expected at `/home/tangzh/data/vlm-training-data-cold-start-portable-20260608/`

### ssh50902 (10.19.138.149) - Data Server

- User: `tangzh`, Password: `4dvlab123`
- 4x RTX 5090 32 GB, 191 GB free for data storage
- Dataset synced via `scripts/scp_training_data.sh` to `/home/tangzh/data/`
- Used as secondary compute node or data staging

<!-- MANUAL: -->
<!-- - Add new game profiles to configs/game_profiles.py following the existing dict format -->
<!-- - All inference server code goes into src/inference/ (coming soon) -->
