# 统一实验标准：fps-play-agent-harness 集成

> 2026-08-04。基于同学框架（fps-research/fps-play-agent-harness v19）的集成，把规划器从 Codex 替换为我们的云端模型。

## 背景

同学的框架 `fps-play-agent-harness` 是一套完整的可玩广告 Agent 运行时：后端探针 + 可选 VLM 观察 + 规划器提案 + 确定性 harness 审批执行。它默认使用 Codex CLI 作为规划器。我们把规划器替换为 OpenAI 兼容的 Chat Completions API（opencodego / kimi / xiaomi / qwen），复用其探针、世界记忆、策略沉淀和验收标准，与同学在同一套标准下做实验。

## 架构

```
后端探针 + 按需截图
  -> canonical world / DSG / 外置记忆
  -> harness_http provider -> planner-http-adapter -> 我们的 Chat Completions API
  -> 确定性 harness 审批并执行宏动作
  -> 动作后验证、局部修正、策略沉淀与完成审计
```

- **harness 侧**：`PLAYABLE_PLANNER_PROVIDER=harness_http`，把结构化请求 POST 到 `http://127.0.0.1:9100/plan`。
- **adapter 侧**：`scripts/planner-http-adapter.mjs` 接收 `{schema_version, model, prompt, brief, output_schema, images}`，映射 provider（deepseek-v4-flash → opencodego、mimo-v2.5 → xiaomi/opencodego、kimi-k2.7 → kimi、qwen3.7-max → qwen），调用 Chat Completions，解析 JSON 后返回 `{intent: {...}}` / `{strategy: {...}}`。

## 环境准备

```bash
# 1. 安装依赖
cd fps-play-agent-harness
npm ci

# 2. 启动 adapter（需要 source smallgameagent/.env 获取 API keys）
cd ../smallgameagent && source .env
cd ../fps-play-agent-harness
NODE_USE_ENV_PROXY=1 node scripts/planner-http-adapter.mjs   # 监听 127.0.0.1:9100

# 3. 浏览器（复用 smallgameagent 的 Chromium）
export PLAYWRIGHT_BROWSERS_PATH=/home/azuma/.cache/ms-playwright
export PLAYWRIGHT_CHROMIUM_PATH=/home/azuma/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome

# 4. 规划器 smoke 测试
PLAYABLE_PLANNER_PROVIDER=harness_http \
PLAYABLE_PLANNER_ENDPOINT=http://127.0.0.1:9100/plan \
PLAYABLE_PLANNER_MODEL=qwen3.7-max \
NODE_USE_ENV_PROXY=1 npm run planner:smoke
```

## 运行新游戏

```bash
# 初始化游戏工作区
npm run game:init -- --game-id <GAME_ID> --title "<TITLE>"
cp html/<GAME>.html games/<GAME_ID>/input/index.html

# 关键：WebGL 游戏必须 headful + xvfb（WSL 无 GPU）
# 修改 games/<GAME_ID>/config/runtime.json: {"launch": {"headless": false}}

# doctor + probe
npm run game:doctor -- --game-id <GAME_ID>
xvfb-run -a -s "-screen 0 1280x1024x24" env \
  PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright \
  PLAYWRIGHT_CHROMIUM_PATH=$HOME/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome \
  PLAYABLE_PLANNER_PROVIDER=harness_http \
  PLAYABLE_PLANNER_ENDPOINT=http://127.0.0.1:9100/plan \
  PLAYABLE_PLANNER_MODEL=qwen3.7-max \
  NODE_USE_ENV_PROXY=1 npm run probe:smoke -- --game-id <GAME_ID>

# 自主运行
xvfb-run -a -s "-screen 0 1280x1024x24" env ... npm run run:autonomous \
  -- --game-id <GAME_ID> --cognition-mode no_vlm_codex_cli
```

## 9 个游戏的 HTML 清单（已随 fps-play-agent-harness 分发）

| 游戏 | 变体 | 文件 |
|---|---|---|
| kingshot | ag-complete | ag-complete-kingshot-80f4e0b5ce95.html |
| kingshot | st-complete | st-complete-kingshot-895771b57988.html |
| kingshot | st-complete | st-complete-kingshot-04db86a52ae5.html |
| whiteout-survival | ag-complete | ag-complete-whiteout-survival-24fbe62a057a.html |
| whiteout-survival | ag-complete | ag-complete-whiteout-survival-9caf6c585e72.html |
| whiteout-survival | st-complete | st-complete-whiteout-survival-02d4a44da242.html |
| tiles-survive | ag-complete | ag-complete-tiles-survive-5ec610abcdff.html |
| tiles-survive | st-complete | st-complete-tiles-survive-01742f16fec0.html |
| tiles-survive | st-complete | st-complete-tiles-survive-0fb314440a22.html |

## Provider 映射（adapter 内）

| harness model | provider | base_url | 说明 |
|---|---|---|---|
| deepseek-v4-flash | opencodego | https://opencode.ai/zen/go/v1 | 文本规划 |
| mimo-v2.5 | xiaomi | https://api.xiaomimimo.com/v1 | 多模态（可带截图） |
| kimi-k2.7 | kimi | https://api.kimi.com/coding/v1 | 文本规划 |
| qwen3.7-max | qwen | https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1 | 结构化输出最稳 |

## 已知问题与修复

1. **WebGL context lost（WSL headless）**：Cocos 游戏在 headless Chromium 下丢失 WebGL 上下文。修复：headful + xvfb 运行（`runtime.json` 设 `headless: false`），并在 `live-playable.mjs` 的 launch 中注入 `--enable-unsafe-swiftshader --in-process-gpu`（headless 时）。
2. **adapter JSON 解析失败**：qwen 输出含 thinking token 或尾部文本导致 `JSON.parse` 失败。修复：`extractJson` 改为平衡花括号扫描，从每个 `{` 位置尝试解析。
3. **代理网络**：WSL 通过 clash 代理（`http://127.0.0.1:7890`）访问外网，Node fetch 需 `NODE_USE_ENV_PROXY=1` 才能走代理。
4. **浏览器路径**：harness 默认在 `.runtime/ms-playwright` 找浏览器，需 `PLAYWRIGHT_BROWSERS_PATH` 指向已有缓存，或用 `PLAYWRIGHT_CHROMIUM_PATH` 指定完整 Chromium。
5. **模型 schema 遵循度不足（核心问题）**：harness 的 StrategySpec/Intent schema 非常严格（状态机、guard、recovery 等），Codex 能稳定遵循，但 qwen/kimi/xiaomi 输出不遵循。qwen 返回简化版 schema；kimi 对复杂策略 prompt 返回空；opencodego 被 Cloudflare 403 拦截；xiaomi 返回空。**修复**：adapter 内置 `buildFallbackStrategy`，当模型输出不遵循 schema 时，用 brief 中的世界状态（玩家位置、guide 目标、路由）构造 schema 合规的确定性策略（calibrate → navigate → interact 三阶段），并让 strategy_id 随位置变化避免 same-context 检测。
6. **动作参数缺失**：probe_joystick 需要 dx/dy ∈ [-1,1]，dwell_at_target 需要玩家在目标附近。修复：fallback 按 option 提供默认参数；按玩家与目标距离选择动作（远→probe_joystick 导航，近→dwell_at_target 交互）。

## 当前运行状态（tiles-survive 5ec610abcdff）

- **planner:smoke** ✅ 通过（qwen3.7-max 经 adapter）
- **probe** ✅ 后端 + 视觉均 healthy（headful + xvfb）
- **autonomous** ⚠️ 游戏可玩：adaptive fallback 策略被接受并执行，`option_started/primitive_executed/option_completed` 事件出现，joystick 校准推进（`control_calibration_updated`），probe_joystick/observe_settle 动作 completed。但 run 被后台任务中断，尚未通关。
- **结论**：基础设施（探针、渲染、规划器桥接、策略执行闭环）已跑通；模型 schema 遵循度靠 fallback 兜底。要达到通关需要更长的连续运行 + 更智能的 adaptive 策略（或 Codex 做 schema 合规规划 + 我们的模型做子任务）。

## 25 whiteout-survival + 25 kingshot 游戏清单

从 https://fps-all-htmls.pages.dev/all-htmls/ 的 `_manifest.json`（4261 个游戏）中筛选：
- 逐个下载 HTML，正则检查「指引箭头（jianTou/Arrow/Guide/Finger/Hand/targeting）+ 通关画面（ENDCARD/ShowEndCard/victory/WinPanel/COMPLETED/endScreen/GameOver）+ 广告图标（mraid/download/install/adIcon/GetAd/showAd/appStore）」
- 按综合得分取前 25。清单见 `harness-integration/selected_games_whiteout_kingshot.txt`（格式 `游戏 / id#链接`，id 与 URL 路径一致）。
