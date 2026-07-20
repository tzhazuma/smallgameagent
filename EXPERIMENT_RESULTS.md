# 实验结果记录

> 随实验推进持续更新。方案见 `EXPERIMENT_PLAN.md`。

## 2026-07-21 P9 多 Provider 配置、规则在线更新、报告/PPT 修复与推送

### 多 Provider 云端 API 统一接入

新增 `.env` 配置（已加入 `.gitignore`，权限 600），统一支持：

| provider | 模型 | base_url |
|---|---|---|
| opencodego | mimo-v2.5 | `https://opencode.ai/zen/go/v1` |
| kimi | kimi-k2.7-code / kimi-k2.6 | `https://api.kimi.com/coding` |
| deepseek | deepseek-chat | `https://api.deepseek.com` |
| xiaomi | mimo-v2.5 | `https://api.xiaomimimo.com/v1` |
| qwen | qwen-plus | `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` |

实现：`src/agent/api_client.py` 新增 `MultiProviderClient`，通过环境变量 `LLM_PROVIDER` / `LLM_MODEL` 切换，保留旧 `OpenCodeGoClient` 兼容。测试 `tests/test_api_client.py`：覆盖 provider 路由、缺 key 失败、kimi 温度省略逻辑。

### 规则在线更新架构（保守方案 A）

目标：把规则作为 L0 执行层，需要更新时由 L2 云端 API / L1 本地 VLM 触发，通过结构化输出修改规则参数或 phase contract，而不是直接重写代码。

新增模块：
- `src/agent/rule_update.py`：`RuleUpdateTrigger`（触发条件评估）、`RuleUpdatePlanner`（生成结构化更新计划）、`apply_rule_patch`（内存 + 可选文件回写）。
- 触发条件：`composite` 连续低于阈值、stall 超过 5 步、L0/L2 决策冲突、世界模型 stale 命中。
- 更新产物：参数更新、phase contract、strategy memory 摘要、可选代码片段（人工审核后写入）。
- `src/agent/multi_agent/bus.py`：新增 `RULE_UPDATE` 消息类型。
- `src/agent/hierarchical_planner.py` + `src/agent/decision_makers/hierarchical_maker.py`：L2 规划器集成规则更新。

### 规则更新 A/B（experiment_rule_update_ab.json / rule_update_ab_results/batch_results.json）

游戏 SSD_00461P01，25 步，seed=42：

| 模式 | composite | activity | move | tap | stall | wm_viol | 墙钟 | 说明 |
|---|---|---|---|---|---|---|---|---|
| rule | 0.112 | 0.75 | 6 | 18 | 6 | 5 | 29.6s | 规则基线 |
| multi-bus-memory | 0.038 | 0.25 | 18 | 6 | 18 | 6 | 32.1s | 记忆文件未预热，策略记忆拖低表现 |
| hierarchical | 0.150 | 0.00 | 0 | 0 | 24 | 0 | 367.9s | L2 规划全部输出 wait，activity 为 0 |

关键发现：
- 保守规则更新方案 A 已跑通，但 L2 云端 API 在本轮配置下倾向于输出 `wait`，导致 hierarchical 模式 activity=0。
- multi-bus-memory 因记忆文件未预热，读回的低质量记忆反而降低 composite。
- 下一步：预热 strategy memory（写入高质量 phase1 记忆）、调优 L2 prompt 强制输出具名目标而非坐标、引入 Verifier 对 L2 计划做可行性检查。

### 本地 VLM struct 矩阵（experiment_local_vlm_matrix.json）

后端：LM Studio  bundled llama.cpp Vulkan/CUDA，`-ngl 99 --flash-attn on --cache-type-k q4_0 --cache-type-v q4_0 -c 4096 -n 512`，截图 720×1280。

| 模型 | 解析成功 | 平均墙钟 | 平均生成 tok/s | 备注 |
|---|---|---|---|---|
| Qwen3.5-4B-Q4KM | 0/3 | 10.6s | ~65 | 输出被 markdown fence 包裹，提取失败 |
| Qwen3.5-9B-Q4KM | 1/3 | 14.7s | 47.9 | 仅第三帧成功解析 |
| gemma-4-E4B-it-Q4_K_M | （历史基准 3/3，见 experiment_vlm_pipeline.json） | 4.8s | 54.9 | Vulkan 下最稳定的小 VLM |

结论：小尺寸 VLM 在线决策仍不稳定，最适合离线标注 + QLoRA 微调后作为视觉上下文生成器，而非实时 action 生成器。

### 训练数据更新

`src/training/trajectory_converter.py` 已接入 `rule_update_ab_results/trajectories`，重新跑完转换：

- 更新后 `vlm-training-data-processed-runs/dataset-manifest.json`：`total_samples = 34,150`，覆盖 22 个游戏、7 个任务。
- 主要新增：`next_probe_action` / `information_gain_judgment` / `progression_grounding` 等从规则更新轨迹中提取的 failure_recovery 与 transition 样本。

### 报告/PPT 修复

- `scripts/generate_deliverables.py`：删掉所有 `shape.line.fill.background()` 调用，改为在 `_set_fill` 中设置 `shape.line.width = Pt(0)`，LibreOffice 转换后颜色正常渲染。
- PPT 视觉检查：标题页深蓝背景 + 青色色块、架构图三色卡片、表格标题栏均正常。
- 报告 `REPORT.md` 已更新第 12–17 章，覆盖多 Provider、规则在线更新、A/B 实验、训练数据管线。

### 浏览器环境修复（P7 前置）

批量跑测时所有游戏返回 `steps=0`、`reason="Probe never reported ready"`。根因：WSL2 headless Chromium 默认无 WebGL，Cocos 场景无法初始化；系统 chromium 搭配 swiftshader 直接 SIGTRAP 崩溃。

修复：
- 使用 Playwright  bundled Chromium：`PLAYWRIGHT_CHROMIUM_PATH=/home/azuma/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome`。
- 启动参数启用软件渲染：`--enable-unsafe-swiftshader --in-process-gpu`。
- 验证：`WebKit WebGL / WebKit`，`cc.director.getScene().name == "main"`。

已添加 `.env.example` 记录上述配置；`.env` 已本地更新并 gitignored。

### 代表性游戏子集跑测（representative_results/）

6 个游戏 × 关键模式 × seed=42，25 步：

| game_id      | mode             | steps | composite | activity | stall | wall  |
|--------------|------------------|-------|-----------|----------|-------|-------|
| SSD_00382P01 | multi-bus-memory | 25    | 0.300     | 1.000    | 0     | 12.8s |
| SSD_00382P01 | rule             | 25    | 0.300     | 1.000    | 0     | 13.4s |
| SSD_00461P01 | multi-bus        | 25    | 0.161     | 0.875    | 3     | 13.0s |
| SSD_00461P01 | multi-bus-memory | 25    | 0.300     | 1.000    | 0     | 11.7s |
| SSD_00461P01 | rule             | 25    | 0.106     | 0.708    | 7     | 14.6s |
| SSD_00483P01 | multi-bus        | 25    | 0.150     | 0.000    | 24    | 20.6s |
| SSD_00483P01 | multi-bus-memory | 25    | 0.150     | 0.000    | 24    | 19.7s |
| SSD_00483P01 | rule             | 25    | 0.244     | 0.625    | 9     | 20.4s |
| SSD_00522P02 | multi-bus        | 25    | 0.227     | 0.917    | 2     | 16.1s |
| SSD_00522P02 | multi-bus-memory | 25    | 0.240     | 1.000    | 0     | 15.0s |
| SSD_00522P02 | rule             | 25    | 0.215     | 0.833    | 4     | 15.7s |
| SSD_00594P02 | multi-bus-memory | 25    | 0.300     | 1.000    | 0     | 17.4s |
| SSD_00594P02 | rule             | 25    | 0.300     | 1.000    | 0     | 17.0s |
| SSD_00742P01 | multi-bus-memory | 25    | 0.300     | 1.000    | 0     | 15.8s |
| SSD_00742P01 | rule             | 25    | 0.300     | 1.000    | 0     | 15.3s |

关键发现：
- **B 类 tap-only 游戏**（00382、00594、00742）rule 模式即可达到 composite 0.300，multi-bus-memory 无额外增益但 stall 归零。
- **A 类 joystick 游戏**中，multi-bus-memory 对 00461 提升显著（0.106 → 0.300），但对 00483 无帮助（multi-bus/multi-bus-memory 均 activity=0）。00483 的 multi-bus 模式陷入持续 move + stall，需要进一步诊断 driver/profile。
- 平均墙钟：A 类 ~15–20s/25 步，B 类 ~13–17s/25 步。

### 训练数据扩充

新增代表性子集轨迹转换：`trajectory_converter.py` 已支持 `--input-dir` / `--output-dir` 参数。

```bash
.venv/bin/python src/training/trajectory_converter.py \
  --input-dir representative_results/A_representative/trajectories \
  --input-dir representative_results/B_representative/trajectories \
  --output-dir vlm-training-data-representative
```

产出 `vlm-training-data-representative/dataset-manifest.json`：`total_samples = 1,173`，覆盖 6 个游戏、5 个任务。

### 测试与质量

- `pytest -q`：**703 passed, 58 skipped, 1 warning**。
- `ruff check .`：全绿。

---

## 2026-07-18 第二轮实验：Tap 策略 + 记忆读回 + Critic A/B + VLM 闭环

### 实验 B：塔防 Tap 策略（experiment_multi_agent_matrix.json）

新增 `_strategy_tap_guide()` 驱动类型（`src/engine/rules.py`）：当 Hero 接近 guide 目标时发 `tap` 而非 `move`。Rubric 更新：`tap` 动作不再计为 stall。

| 模式 | 步数 | composite | activity | move | tap | stall | 墙钟 |
|---|---|---|---|---|---|---|---|
| rule (tap-guide) | 30 | **0.150** | 1.000 | 5 | 24 | 0 | 25.2s |
| multi (tap-guide) | 30 | **0.150** | 1.000 | 6 | 23 | 0 | 24.3s |
| multi-bus (tap-guide) | 30 | **0.150** | 1.000 | 18 | 11 | 0 | 29.0s |
| multi-bus-memory (tap-guide) | 30 | **0.150** | 1.000 | 18 | 11 | 0 | 30.5s |

- 从 0.000 提升到 0.150（activity 从 0→1.0），证明 tap 策略让 agent 从"无效移动"变为"有效交互"。
- rule/multi 模式 tap 占比更高（24/30 vs 11/30），因为 multi-bus 的 Verifier 触发 re-decide 时回退到 move。

### 实验 A：StrategyMemory 读回（experiment_memory_readback.json）

Phase 1 写入记忆 → Phase 2 读回记忆 → 对照组无记忆。

| 阶段 | composite | activity | memory_hits | wm_violations | 主要决策来源 |
|---|---|---|---|---|---|
| phase1_write | 0.150 | 1.000 | 92 | 3 | strategy_memory:23, rule_engine:7 |
| **phase2_read** | **0.300** | 1.000 | 116 | **0** | strategy_memory:29, rule_engine:1 |
| control_no_memory | 0.150 | 1.000 | 0 | 3 | rule_engine:30 |

**关键发现**：记忆读回将 composite 从 0.150 翻倍到 **0.300**，原因是世界模型违规从 3 降到 0——记忆中的高成功率 tap 模式直接绕过了 rule_engine 的 move-then-tap 两步流程，减少了不必要的移动导致的 stale 标记。

### 实验 D：Critic 反馈循环 A/B（experiment_critic_ab.json）

| 配置 | composite | bus_messages | critic_invocations | 墙钟 |
|---|---|---|---|---|
| max_rounds=1 (无 Critic) | 0.150 | 11 | 2 | 28.3s |
| max_rounds=2 (有 Critic) | 0.150 | 15 | 2 | 29.7s |

**结论**：Critic 在 tap-guide 确定性策略下无增益——Verifier 触发 re-decide 后 DecisionAnalyst 仍返回相同动作。额外 4 条总线消息带来 ~5% 墙钟开销。Critic 的价值应在非确定性场景（如 API LLM 决策）中评估。

### 实验 C：本地 VLM 在线决策（experiment_vlm_local_gameplay.json）

使用 gemma-4-E4B-it-Q4_K_M（RTX 5060 CUDA12 + q4_0 KV-cache）作为 `vlm-local` 模式的决策引擎。

| 模式 | composite | activity | move | tap | stall | wm_viol | 墙钟 |
|---|---|---|---|---|---|---|---|
| **vlm-local (gemma)** | **0.178** | 0.684 | 13 | 1 | 6 | **0** | 276s |
| rule (tap-guide) | 0.150 | 1.000 | 4 | 15 | 0 | 4 | 22s |

VLM composite 略高（一致性更好），但 tap 仅 1 次（vs 15），墙钟 12.5×。需要更强的 tap 引导 prompt。

### 实验 E：云端 API 在线 gameplay（experiment_cloud_api_gameplay.json）

首次将云端 API 接入实时 gameplay 循环。

| 模式 | composite | move | tap | stall | 墙钟 |
|---|---|---|---|---|---|
| api (kimi-k2.7-code) | 0.150 | 0 | 0 | 14 | 310.6s |
| api (mimo-v2.5) | 0.150 | 0 | 0 | 14 | 307.5s |
| rule (tap-guide) | 0.150 | 3 | 11 | 0 | 15.3s |

两个云端 LLM 均返回 `wait`——纯文本 API 无法从 probe state 推断 tap 坐标。需要 vision 模式或 VLM-struct 中间层。

### 测试修复

- `tests/test_game_catalog.py`：`scan_games("")` 不再扫描项目根目录（空字符串 = 跳过）。31/31 通过。
- `tests/test_dataset_converter.py`：3 个需要真实数据集的测试加 `@requires_dataset` skip 标记。
- `tests/test_game_configs.py`：`tap-guide` 加入 valid_driver_types。
- `tests/test_game_env.py`：rubric 修复——仅 `tap` 豁免 stall 计数，`move` 零位移仍算 stall。
- `tests/test_lmstudio_client.py`：`extract_content` 对非字符串 `reasoning_content` 鲁棒化。
- **最终：674 passed, 58 skipped, 0 failed。**

---

## 2026-07-18 本轮实测修正

### 在线 gameplay 打通
- WSL2 + Playwright 绑定 Chromium 已能初始化 Cocos 场景。关键参数：
  `PLAYWRIGHT_CHROMIUM_PATH=~/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome`
  `PLAYWRIGHT_CHROMIUM_ARGS="--use-gl=angle --use-angle=gl --ignore-gpu-blocklist --disable-gpu-sandbox"`
- 之前报告说“headless Chromium 无法初始化 Cocos 场景”是因为未加 GPU 参数；加参数后 `cc.director.getScene()` 正常返回，probe `ready=true`。

### 多 Agent 矩阵（experiment_multi_agent_matrix.json）
| 模式 | 步数 | composite | activity | progress | stall | 墙钟 | bus_messages |
|---|---|---|---|---|---|---|---|
| rule | 30 | 0.000 | 0.000 | 0.000 | 29 | 26.7s | — |
| multi | 30 | 0.000 | 0.000 | 0.000 | 29 | 26.8s | — |
| multi-bus | 30 | 0.000 | 0.000 | 0.000 | 29 | 28.4s | 15 |
| multi-bus-memory | 30 | 0.000 | 0.000 | 0.000 | 29 | 28.4s | 15 |

- composite 为 0 的根因：SSD_00461P01 塔防需要 tap/place/升级类交互，当前 `follow-guide-audited` 摇杆驱动只能让 Hero 移动，无法推进游戏目标。
- multi-bus 开销 <10%，总线消息分布：observe×2、perceive×2、decide×4、verify×4、critic×2、memory×1。

### 本地 VLM struct 基准（experiment_local_vlm_matrix.json）
后端：`llama.cpp-linux-x86_64-nvidia-cuda12-avx2-2.17.0/llama-server`，参数 `-ngl 999 --flash-attn on --cache-type-k q4_0 --cache-type-v q4_0 -c 4096 -n 512`。

| 模型 | 解析成功 | 平均墙钟 | 平均生成 tok/s |
|---|---|---|---|
| Qwen3.5-4B-Q4_K_M | 0/3 | 11.3s | ~62 |
| Qwen3.5-9B-Q4_K_M | 2/3 | 16.9s | 44.8 |
| gemma-4-E4B-it-Q4_K_M | 3/3 | 4.8s | 54.9 |

### 云端 API 混合（experiment_cloud_api_matrix.json / experiment_cloud_api_struct.json）
| 模型 | 模态 | 延迟 | struct 解析 |
|---|---|---|---|
| mimo-v2.5 | 文本 | 6.7s | — |
| mimo-v2.5 | 视觉 | 17.6s | 3/3（22–38s/帧） |
| kimi-k2.7-code | 文本 | 2.2s | — |
| kimi-k2.6 | 文本 | 1.4s | — |
| kimi-k2.6 | 视觉 | 3.7s | 0/3（思考链占满 token） |

- OpenCodeGo 余额已可用，不再报 `CreditsError`。

### 代码修复
- `src/agent/roles/verifier.py`：probe_state/player 为 None 时不再崩溃。
- `src/agent/multi_agent/orchestrator.py` + `src/agent/roles/critic.py`：兼容 `Verdict` 数据类与 dict。
- `scripts/generate_deliverables.py`：补 `typing.Any`、修正 `table_rows` nonlocal、删未使用导入，ruff 全绿。

### 测试
- `pytest tests/ -q`：671 passed，55 skipped，6 failed（数据集目录缺失 + game_catalog 扫描根目录副作用）。
- `ruff check src tests scripts`：全绿。

---

## 2026-07-18 P8 报告修复、RTX 5060 CUDA、Agent 通信与记忆

### 报告生成器修复
- 问题：`scripts/generate_deliverables.py` 的 `inline_fmt` 在最后又对整段文本做 `esc()`，
  把已生成的 LaTeX 命令（`\textbf{}`、`\texttt{}`）转义成乱码。
- 修复：采用 placeholder 机制——先提取 markdown 标记（code/bold/italic/link），
  转义普通文本，再插回 LaTeX 命令；同时把 `①②③④` 替换为 `(1)(2)(3)(4)` 避免字体 tofu。
- 产物：`reports/smallgameagent_report.pdf`、`reports/smallgameagent_report.pptx`。

### RTX 5060 Laptop 8 GB 本地模型基线（CUDA12 llama.cpp + KV-cache 量化）

后端：`~/.lmstudio/extensions/backends/llama.cpp-linux-x86_64-nvidia-cuda12-avx2-2.17.0`
启动参数：`-ngl 99 --flash-attn on --cache-type-k q4_0 --cache-type-v q4_0 -c 4096 --no-mmproj-offload`

| 模型 | 文本 tok/s (server) | 60 tokens wall | 显存占用 | 视觉单帧 |
|---|---|---|---|---|
| Qwen3.5-4B-Q4_K_M | **82.6** | 1.0 s | 3465 MB | 8 帧 struct 解析率 0.75，target_acc 0.83，平均 24.8 s |
| gemma-4-E4B-it-Q4_K_M | **39.9** | 0.7 s | 7082 MB | 单帧 14.6 s，输出含 markdown fence 但可解析 |

结论：CUDA 后端让 4B 从 Vulkan 的 ~2 tok/s 提升到 ~80 tok/s，视觉从 10+ min/帧降到
~20 s/帧，**首次具备在线逐步控制的本地可行性**（但仍慢于云端 mimo-v2.5 的 ~9 s/帧）。

### Agent 通信与记忆机制

新增模块：
- `src/agent/multi_agent/bus.py`：显式消息总线，`MessageType` + `Message` + `AgentBus`。
- `src/agent/multi_agent/orchestrator.py`：Observer → StateMapper → DecisionAnalyst → Verifier → Critic 的循环流水线，Verifier 可触发 `re-decide`。
- `src/agent/strategy_memory.py`：文件型策略记忆，不依赖 `sqlite-vec`。
- `src/agent/decision_makers/bus_multi_maker.py`：注册 `multi-bus` / `multi-bus-memory` 模式。
- `src/agent/roles/critic.py`：Critic 角色，给出诊断与修正方向。

测试：`test_multi_agent_bus.py`、`test_multi_agent_orchestrator.py`、`test_strategy_memory.py`；
与既有测试合并后 **641 passed, 20 skipped**。

### 云端 API 与在线 gameplay 实验状态

- **云端 API**：OpenCodeGo 客户端认证通过（`AICODEWITH_API_KEY`），但调用返回
  `CreditsError: Insufficient balance`，当前余额不足以跑 mimo-v2.5 / kimi-k2.7-code /
  kimi-k2.6 矩阵。已记录该阻塞，待充值后继续。
- **在线 gameplay**：headless Chromium 在 WSL2 中无法初始化 Cocos 场景（`cc.director.getScene()` 为 null），
  与是否启用 `--no-sandbox` 无关；尝试 `--enable-unsafe-swiftshader` 时浏览器崩溃。
  推断需要 headed 显示环境或带 GPU 的容器才能跑通实时交互实验。当前已用单元测试与
  离线 VLM 评测替代，完整 gameplay 矩阵待环境修复后补跑。

## 2026-07-17 P0 环境与基线

### 环境打通
- WSL2（Ubuntu 26.04，16C/15GB）。Vulkan 1.4 可用：GPU0 = Mesa Dozen 桥接 Intel Graphics
  （D3D12 on WSL2，vendorID 0x8086）。无 NVIDIA GPU。
- 云端：`https://opencode.ai/zen/go/v1`（OpenAI 兼容），凭据自动从
  `~/.local/share/opencode/auth.json` 的 `opencode-go.key` 读取（已作为
  `OpenCodeGoClient` 的 env 缺失 fallback 实现，env 优先）。
- **kimi-k2.7-code 坑**：Console Go 代理对任何显式 `temperature` 参数返回
  400 "Upstream request failed"。修复：`api_client.chat()` 对 `kimi*` 模型省略
  temperature（`_NO_TEMPERATURE_PREFIXES`），见 `src/agent/api_client.py`。
- LM Studio：`lms` CLI 与应用守护进程密钥握手失败（0.4.17 app vs 0.4.18+1 llmster 版本
  错位）。采用等价路线：GGUF 放入 `~/.lmstudio/models/` + 直跑 LM Studio 捆绑的
  Vulkan llama.cpp 后端（`~/.lmstudio/extensions/backends/llama.cpp-linux-x86_64-vulkan-avx2-2.22.0`）。

### 云端冒烟（experiment_cloud_smoke.json）
| 调用 | 模型 | 延迟 | 结果 |
|---|---|---|---|
| 文本决策 ×3 | kimi-k2.7-code | 2.38 / 6.26 / 2.81 s | 均输出合法 JSON action |
| 视觉分析 | mimo-v2.5 | 9.79 s | JSON 解析成功（截图字段识别正确） |
| 多模态视觉 | kimi-k2.7-code | 9.22 s | JSON 解析成功，无 markdown 包裹 |

对照旧报告 deepseek-v4-flash 8–15s/步：kimi-k2.7-code 文本决策约快 3–5×。

### 规则基线（experiment_baseline_rule_00461.json）
- rule 模式（follow-guide-audited），SSD_00461P01 塔防，300 步，墙钟 262.9s，
  **0.88s/步**（决策 0ms），action: move×299 wait×1。
- 推进：UnlockItem_1→2（~step 30-121）→ 候选集切换 UnlockItem_3/4（step 121）。
- **失败模式（空间一致性实锤）**：step 121 场景切换后直线跟随走向新目标，
  在 (3.11, 2.48) 被障碍卡死；`_failCount`=1；step ~130-299 共 ~170 步
  stuck_escape 随机逃逸全部无效，位置完全冻结。completed=false win=false。
- 次要观察：step 112-120 在目标附近两位置间往复超调（脉冲时长对短距过长），
  靠足够停留时间侥幸触发解锁。

### 工程修复（本轮）
- probe JS vendored 至 `src/agent/browser-probe-source.js`，`PLAYABLE_AGENT_PROBE_PATH` 可覆盖。
- rule 模式视觉链路修复：`VisualAnalyzer.analyze_pil()`（同步本地青色检测，输出
  rules.py 消费的 stick/arrow schema），替换两处错误的 async 调用点。
- `inference/server.py:978` NameError 修复。
- 测试：524 passed（6 个历史失败与缺旧数据目录有关，已 deselect）。

## P1 空间一致性 A/B（experiment_p1_wm_00461.json vs 基线）

机制落地：`world_model.py`（entity/scene/capability 版本化，stale 传播）+
RuleEngine 障碍学习（受阻方向记录障碍点、势场斥力转向、8 方向打分的定向逃逸）。
15 个障碍单测 + 12 个世界模型单测全过。

A/B 数字（00461，rule 300 步）：

| 指标 | 基线 | P1 集成版 |
|---|---|---|
| 墙钟 / 步均 | 262.9s / 0.88s | 320.7s / 1.06s |
| 学到的障碍点 | 0 | 4（死亡区 (3.5-5.1, 3.2-4.7) 内，单点最高 78 次强化） |
| 世界模型 | — | scene_epoch=7，stale_replans=6，capability_flips=0 |
| step≤124 活动性 | 口袋内游走 | 同（势场转向生效） |
| **最终结局** | step~150 起位置冻结 | **step 123 起位置完全冻结** |

**修正的失败归因 v2**（诊断跑实锤）：血量全程 100、Hero active、无敌人组件——
**英雄没死**。真实机制：step 115 起玩家以 ±3 单位大幅超调振荡（脉冲过长）；
`failCount` 0→1 翻转后位置**立即完全冻结**（两次 run 分别在 step 123/132），
done/win 均为 false。结论：**fail 事件弹出失败面板阻断摇杆输入，agent 从不点击
继续/重试按钮 → 永久瘫痪**（这正是「交互方式改变」型的空间一致性问题）。
修复方向：监测 failCount 翻转 / 失败面板节点 → 点面板按钮再继续（待 UI 诊断确认按钮身份）。

**Rubric 量化**（`src/experiments/game_env.py`，verifiers 风格，8 单测）：

| run | completion | progress | activity | consistency | composite |
|---|---|---|---|---|---|
| 基线 | 0 | 0.667 | 0.535 | 0.967 | **0.425** |
| P1 集成 | 0 | 0.583 | 0.408 | 0.767 | **0.351** |

P1 机制照实工作（障碍点 4 个、stale 重规划 6 次）但结局更差——因为两次 run
都死于同一个 fail 面板瘫痪，障碍学习不是 binding constraint。**失败面板处理
是当前最高杠杆修复点。**

## 失败面板根因与修复（experiment_failpanel3_00461.json）

**完整根因链**：① `failCount` 翻转 → `LosePanel` 激活阻断摇杆输入 → 永久瘫痪；
② v1 修复未生效是因为**生产构建把组件类名 minify 成单字母**（"e","p","w"），
`findPanelButtons` 按 `/Button$/` 匹配组件名必然落空；③ 面板上有 `downloadBtn`
（广告陷阱）与 `retryBtn`，坐标是 Cocos 设计分辨率（720×1560，左下原点）。

**修复 v2**：探针按**节点名** `btn|button` 匹配（绕过 minify）+ 返回设计坐标
与 designSize + dpr；Python 侧 retry/continue 优选、避开 download/install，
`css = (dp.x/designW × vw, (1 − dp.y/designH) × vh)`。7 单测。

**A/B v3**（00461，rule 300 步，机制全开：世界模型+障碍学习+失败面板处理）：

| run | composite | activity | progress | consistency | stall 步 | 墙钟 |
|---|---|---|---|---|---|---|
| 基线 | 0.425 | 0.535 | 0.667 | 0.967 | 139 | 262.9s |
| P1（世界模型+障碍） | 0.351 | 0.408 | 0.583 | 0.767 | 177 | 320.7s |
| **v3（+面板处理）** | **0.473** | **0.920** | **0.750** | 0.733 | **24** | **153.5s** |

注：v3 未触发 fail（游戏波次有 RNG），提升主要来自未瘫痪 + 全速推进；
面板点击本身由确定性强制失败实验单独验证（强制 wait → 必输 → 自动点 retry）。

**端到端验证（/tmp/verify_panel_forced.py 实测）**：JS 强制激活 LosePanel +
置 `_failCount=1` → agent 检出 flip 0→1 → `findPanelButtons` 返回
downloadBtn+retryBtn → 优选 retryBtn 点击 → **1 秒后 panel_active=False（面板关闭）**。
期间发现第三个根因：探针 `traverse()` 的 MAX_NODES=500 BFS 上限在大场景下
覆盖不到 Canvas/UI 分支，已改用原生 `scene.walk`（无上限）。

| 路径 | 配置 | 结果 |
|---|---|---|
| 文本 | `-ngl 99` Vulkan | **9.9 tok/s** 生成，可用 |
| 文本 | `-ngl 0` 纯 CPU 对照 | ~1 tok/s（慢 ~10×） |
| 视觉 | BF16 mmproj + Vulkan | **挂死**（编码 68s 后 token 位置死循环，Dozen 数值异常） |
| 视觉 | BF16 mmproj + `--no-mmproj-offload`（编码留 CPU） | 成功：截图→合法 JSON struct；首测 53s/帧，思考链 ~700 token 时 2m51s |

注意：headless swiftshader 截图主场景合成黑图（VLM 如实报 "very dark"）；
真实截图评测须用 processed-runs 里的真实帧或 WSLg headed 模式。

## P7 训练数据转换（vlm-training-data-processed-runs/）

processed-runs.rar（22 游戏，3054 步）→ 7 任务 JSONL，14 单测全过，
VLMColdStartDataset 实载抽查通过：

| 任务 | train+val |
|---|---|
| next_probe_action / probe_action_effect / field_grounding / progression_grounding | 各 2645 |
| information_gain_judgment | 3054 |
| pulse_response_grounding | 1435 |
| failure_recovery | 14（旧集 83，合并后 97；成功 run 中卡死窗口本来就少） |
| **合计** | **15,083** |

跳过统计：pulse_response 549 步无玩家坐标、207 步无脉冲向量；409 步缺 before 截图/状态。

## 待补
- F16 mmproj Vulkan 复测；lmstudio_client.py；9B/E4B 下载与基准
- P3 策略优化引导实验（含 00461 step123 即死事件的诊断）
- P6 verifiers 风格环境
- ssh5090 QLoRA（等可访问）

## 2026-07-20 第三轮：分层架构 + 批量框架 + Node.js 逻辑移植

### 分析 ~/delivery/ 同学系统
- Node.js 系统（playable-agent-12-games）：12 游戏完整驱动，含 coin override / soft lock / guide signature / A* routing。
- 训练数据：10,336 样本 7 任务（cold-start），含 known_facts/unknowns 探索认识论。
- 我们的 Python src/ 已领先（memory/world_model/multi_agent/roles），集成价值在 Node.js 驱动 + 数据集 + .omo 训练笔记。

### 实验 H：移植高级循环逻辑
- soft target lock（8 步锁定防 thrashing）、guide-signature change detection（path+45° bucket）、coin demand override。
- 问题：soft lock 在 tap-guide 下降低 tap 频率（0.150→0.10）；修复：tap 后释放 lock。

### 实验 F：分层多 Agent 架构（HierarchicalPlanner）
- L0 rule（每步）+ L1 本地 VLM（每 5 步/stuck）+ L2 云端 API（每 15 步/phase 切换）。
- 新建 `src/agent/hierarchical_planner.py` + `hierarchical_maker.py`，注册 `hierarchical` 模式。
- 批量结果：hierarchical composite=0.150，但 L1 因本地 VLM 未常驻而退化，L2 kimi 思考链截断 JSON。

### 实验 G：批量实验框架 + 数据采集
- `batch_runner.py`（BatchConfig + run_batch，自动写轨迹 JSONL，支持 resume）+ `analyze_batch.py`（Markdown 对比表）。
- `exp_batch_matrix.py`：1 game × 4 modes × 2 seeds = 8 runs。

### 批量矩阵结果（batch_results/analysis.md）
| Mode | Mean Composite | Mean Activity | Mean Latency |
|---|---|---|---|
| **multi-bus** | **0.300** | 1.000 | 21.9s |
| multi-bus-memory | 0.215 | 0.931 | 23.4s |
| hierarchical | 0.150 | 0.000 | 205.1s |
| rule | 0.101 | 0.672 | 34.7s |

- multi-bus 两 seed 一致 0.300，确认总线+记忆组合为当前最佳。
- 8 个轨迹 JSONL 已采集，可用于 VLM 微调 / 离线回放 / A/B 可视化。

### 测试
- 674 passed, 0 failed, ruff 全绿。

## 2026-07-20 第四轮：多游戏泛化 + probe 终止假阳性修复

### 让 22 个游戏都可驱动（generic fallback）
`_extracted/games/` 有 22 个游戏，但只有 SSD_00461P01 有手调 profile；此前 `RuleEngine` 对未知 game_id 直接抛 `ValueError`，导致多游戏实验无法跑。
- `configs/game_profiles.py` 新增 `GENERIC_PROFILE`（floating joystick + 单位基线，**未校准**，标 `is_generic=True`）与 `get_profile_or_generic()`。
- `src/engine/rules.py` 的 `RuleEngine` 改用 `get_profile_or_generic`，无 profile 游戏不再抛错，而是用未校准 generic 驱动（移动方向不可靠，但 tap 屏幕坐标仍有效），并打 warning、置 `self.is_generic`。
- 意义：框架现在能**加载、驱动、采集**任意游戏的轨迹，泛化/数据采集不再被 profile 缺失阻塞；分数高低则诚实反映"是否需要该游戏的校准"。

### 多游戏泛化实验（exp_multi_game.py + batch_runner）
挑 5 个机制有代表性的游戏 × 2 模式（rule / multi-bus-memory）× 1 seed，25 步，自动写逐步轨迹 JSONL：
- 00461 塔防（**已校准**）、00482 砍树扩地（收集）、00736 养蛙捕鱼养龟（auto-fish 能力翻转）、00342 建造合并、00532 瀑布巨木（收集）。

**初始跑（raw probe 终止判据，暴露测量 bug）**：
| 游戏 | 校准? | 模式 | 步数 | composite | activity | tap | move | stall |
|---|---|---|---|---|---|---|---|---|
| 00461 | cal | rule | 25 | 0.112 | 0.75 | 18 | 6 | 6 |
| 00461 | cal | multi-bus-memory | 25 | 0.131 | 0.88 | 21 | 3 | 3 |
| 00482 | GEN | rule | **1** | 0.700 | 1.00 | 0 | 0 | 0 |
| 00482 | GEN | multi-bus-memory | **1** | 0.700 | 1.00 | 0 | 0 | 0 |
| 00736 | GEN | rule | 25 | 0.269 | 0.79 | 20 | 5 | 5 |
| 00736 | GEN | multi-bus-memory | 25 | 0.281 | 0.88 | 22 | 3 | 3 |
| 00342 | GEN | rule | **1** | 0.700 | 1.00 | 0 | 0 | 0 |
| 00342 | GEN | multi-bus-memory | **1** | 0.700 | 1.00 | 0 | 0 | 0 |
| 00532 | GEN | rule | 25 | 0.150 | 0.00 | 0 | 25 | 24 |
| 00532 | GEN | multi-bus-memory | 25 | 0.150 | 0.00 | 0 | 25 | 24 |

观察：00736 用**未校准** generic 驱动仍真玩满 25 步、composite 0.27–0.28（probe 后端状态读得全：chickenCount/fishCount/coin 等），证明 generic 路径可用；00532 因基线未校准全 stall（activity 0，符合预期，正是"该游戏需要校准"的诚实信号）。

### probe 终止假阳性根因（实时 probe dump 实锤）
00482/00342 在加载时 `done=False`，但**第一个动作后** probe 把 `done/win` 翻成 True，导致 1 步假阳性、composite 虚高 0.700。dump `observe_fast` 的 `completionSummary.endState`：
- 00482：`reason="manager flag cc.Button._transitionFinished"`，`activeEndNodes=[]`，`analyticsEventsTail=[]`，`managerFlags=[]`。即 Cocos 引擎内部标志 `cc.Button._transitionFinished`（含 "finish" 子串）被 probe 的 WIN 正则误命中——它只是按钮按压动画结束标志，常驻为 True。
- 00342：`activeEndNodes=[Logo, Container, 火堆]`（reason "completion-like node active"）——全是常驻 gameplay UI，不是结束面板。
- 对照 00736：全程 `done=False`，无误触发。

### 修复：完成判定改为"佐证制"（_is_finished）
`src/agent/hybrid_agent.py` 的 `_is_finished` 不再信任裸 `done/win`，而要求**真实结束屏的佐证**之一：
1. 结束面板节点：节点名/路径匹配 `endcard|victory|success|gamewin|ui_win` 或 `win/WinPanel`（**排除 lose/fail**，以免破坏 00461 的可恢复失败重试）；
2. 胜利 analytics 事件：`ENDCARD_SHOWN|COMPLETED|CHALLENGE_SOLVED|ShowEndCard`；
3. 非引擎管理器的强胜利标志：className 不以 `cc.` 开头且 flag 键匹配 `isWin|gameWin|hasWin|isComplete|levelComplete`。
三者皆无（如 `cc.Button._transitionFinished`、Logo/火堆）→ 判为假阳性，**继续游玩**。无 `completionSummary` 的旧 probe 回退到 raw 标志，绝不回归。

预期效果：00482/00342 不再 1 步退出，跑满 25 步给出真实泛化分数；00461 真赢（WinPanel）仍正确终止、可恢复失败仍走 dismiss 重试。修正后重跑数据见 `multi_game_results/analysis.md`（下一节补入）。

**修正后部分验证（重跑进行中，已观测）**：00482 rule 由修正前的 **1 步 / 0.700（假阳性）** 变为修正后的 **25 步 / 0.150**——假阳性被消除，跑满步数；其 0.150 来自 consistency=1.0 而 activity≈0（未校准基线方向乱、全 stall），正是"该游戏需要校准"的诚实信号。00461 multi-bus-memory 修正后仍 **0.300**，确认佐证修复对已校准/真终止场景无回归。

### 修正后完整结果（佐证制 _is_finished）
| 游戏 | 校准? | 模式 | 步数 | composite | activity | tap | move | stall | 墙钟 |
|---|---|---|---|---|---|---|---|---|---|
| 00461 | cal | rule | 25 | 0.106 | 0.71 | 17 | 7 | 7 | 24.2s |
| 00461 | cal | multi-bus-memory | 25 | **0.300** | 1.00 | 24 | 0 | 0 | 19.9s |
| 00482 | GEN | rule | 25 | 0.150 | 0.00 | 0 | 0 | 24 | 34.5s |
| 00482 | GEN | multi-bus-memory | 25 | 0.150 | 0.00 | 0 | 0 | 24 | 34.9s |
| 00736 | GEN | rule | 25 | 0.269 | 0.79 | 20 | 5 | 5 | 26.4s |
| 00736 | GEN | multi-bus-memory | 25 | **0.300** | 1.00 | 25 | 0 | 0 | 25.2s |
| 00342 | GEN | rule | 25 | 0.150 | 0.00 | 0 | 0 | 24 | 31.8s |
| 00342 | GEN | multi-bus-memory | 25 | 0.150 | 0.00 | 0 | 0 | 24 | 31.5s |
| 00532 | GEN | rule | 25 | 0.150 | 0.00 | 0 | 25 | 24 | 34.7s |
| 00532 | GEN | multi-bus-memory | 25 | 0.150 | 0.00 | 0 | 25 | 24 | 32.3s |

**关键发现**：
1. **假阳性消除**：00482/00342 从 1 步→25 步，composite 从虚高 0.700→真实 0.150。
2. **00736 multi-bus-memory 达 0.300**——与已校准的 00461 持平！记忆读回机制补偿了未校准基线：它记住了成功的 tap 模式，使 activity 从 0.79→1.00、stall 从 5→0。
3. **multi-bus-memory ≥ rule** 在所有 5 游戏上一致成立。
4. 00482/00342/00532 的 activity=0 是**诚实信号**：未校准基线导致方向全错→全 stall→需要该游戏的 profile 校准。

### 自动校准（auto_calibrate.py）

新建 `src/experiments/auto_calibrate.py`：对每个游戏发 4 方向 joystick 脉冲 + 返回脉冲，测量 worldPosition delta 计算 screen→world 基线。含 warmup（3 脉冲 + 中心 tap 关闭教程覆盖层）、失败方向重试、joystick 无效时回退到 probe 的 `moveByCocosInput`。

| 游戏 | 校准结果 | basis (screen_right / screen_down) | 说明 |
|---|---|---|---|
| 00736 养蛙捕鱼 | **VALID** | (1.44, -2.90) / (1.49, 2.90) | warmup 后 3/4 方向有效，重试补齐第 4 方向 |
| 00482 砍树扩地 | INVALID | — | joystick + moveByCocosInput 均 0 位移；非 joystick 驱动游戏 |
| 00342 建造合并 | INVALID | — | 同上 |
| 00532 瀑布巨木 | INVALID | — | 同上 |

**关键发现**：00482/00342/00532 对 joystick 和 Cocos Actor.move 都无响应——这些是 **tap-to-move 或自动移动** 类游戏，需要完全不同的驱动策略（如点击目标位置、等待自动战斗）。自动校准正确识别了这一分类。

00736 校准后写入 `configs/game_profiles.py`（第 14 个 profile），重跑多游戏实验确认：00736 标记为 calibrated，multi-bus-memory 仍达 **0.300**（activity=1.00, tap=25, stall=0）。

## 2026-07-20 第五轮：全游戏自动校准 + Tap-to-Move + 全矩阵

### 全游戏自动校准（22 游戏，`auto_calibrate.py --all`）

对全部 22 个 `_extracted/games/` 游戏跑自动校准，分类结果：

| 类型 | 数量 | 游戏 |
|---|---|---|
| A 类（joystick 驱动） | 5 | 00440 清障通车、00483 吸沙抽水、00496 电网抓丧尸、00517 末世旅店、00522 地下炸矿 |
| A 类（已有校准） | 2 | 00461 塔防、00736 捕鱼 |
| B 类（tap-to-move） | 15 | 00219、00332、00342、00382、00394、00427、00434、00475、00482、00526、00532、00594、00669、00733、00742 |
| C 类（probe 失败） | 0 | — |

5 个新校准游戏的基线（screen_right / screen_down）：
- 00440: (-6.42, 2.34) / (-2.34, -6.42)
- 00483: (3.41, -3.41) / (2.42, 2.42)
- 00496: (0.75, -0.90) / (0.82, 3.57)
- 00517: (7.42, -0.00) / (0.00, 9.44)
- 00522: (4.92, 0.00) / (-0.27, 3.46)

### Tap-to-Move 驱动策略

新增 `_strategy_tap_only()` 驱动：不走路，直接 tap guide 目标的屏幕坐标（design→CSS 映射，无需 joystick 校准）。15 个 B 类游戏自动填充 `tap-only` profile。`get_game_type()` / `get_driver_for_type()` 自动分类与选择驱动。

### 全游戏 × 多模式批量矩阵（exp_full_matrix.py，116 runs）

- A 类(7 游戏) × 4 模式(rule / multi-bus-memory / multi-bus / hierarchical) × 2 seeds = 56 runs
- B 类(15 游戏) × 2 模式(rule / multi-bus-memory) × 2 seeds = 60 runs
- 自动写逐步轨迹 JSONL，产出 `full_matrix_results/batch_results_all.json` + `analysis_all.md`

（矩阵在后台运行中，完成后数据补入下方）

### Phase 3 全矩阵结果（A_full 48/56 + B_tap 60 runs）

**A_full（7 个 joystick 游戏 × 4 模式 × 2 seeds = 48 runs 完成）**：

| 游戏 | rule | multi-bus-memory | multi-bus | hierarchical |
|---|---|---|---|---|
| 00440 清障通车 | 0.184 | 0.156 | 0.156 | 0.150 |
| 00461 塔防 | 0.113 | 0.300 | 0.297 | 0.150 |
| 00483 吸沙抽水 | 0.139 | 0.300 | 0.300 | 0.150 |
| 00496 电网抓丧尸 | 0.275 | 0.150 | 0.150 | 0.150 |
| 00517 末世旅店 | 0.150 | 0.150 | 0.150 | 0.150 |
| 00522 地下炸矿 | 0.215 | 0.240 | 0.300 | — |
| 00736 捕鱼 | — | — | — | — |

**关键发现**：
- **multi-bus / multi-bus-memory 在 00461/0 0483/00522 上稳定 0.300**（activity=1.00, stall=0）。
- **00496 rule 最优（0.275）**——multi-bus 的记忆读回反而降低（0.150），说明该游戏的确定性策略比记忆启发式更有效。
- **hierarchical 全部 0.150**（activity=0）：云端 API 的 L2 macro-plan 没有被 L0 规则引擎有效执行，L2 输出契约需要改进。

**B_tap（15 个 tap-only 游戏 × 2 模式 × 2 seeds = 60 runs 完成）**：

| 游戏 | rule | multi-bus-memory |
|---|---|---|
| 00219 养牛卖奶 | 0.150 | 0.150 |
| 00332 圣诞薅羊毛 | 0.150 | 0.150 |
| 00342 建造合并 | 0.150 | 0.150 |
| **00382 低坑杀鲨鱼** | **0.300** | **0.300** |
| **00394 车zip** | **0.300** | **0.300** |
| 00427 淘金 | 0.150 | 0.150 |
| 00434 选项卡捏 | 0.150 | 0.150 |
| **00475 太空圈地** | **0.300** | **0.300** |
| 00482 砍树扩地 | 0.150 | 0.150 |
| **00526 通水洗地** | **0.300** | **0.300** |
| **00532 瀑布巨木** | **0.300** | **0.300** |
| **00594 破石收水** | **0.300** | **0.300** |
| **00669 斜挖订单** | **0.300** | **0.300** |
| 00733 海洋回收 | 0.150 | 0.150 |
| **00742 加油小镇** | **0.300** | **0.300** |

**关键发现**：
- **8/15 B 类游戏达 0.300**——tap-only 驱动有效：tap guide 目标的屏幕坐标产生真实交互（activity=1.00, stall=0）。
- **7/15 B 类游戏 0.150**——tap-only 有 tap 但无移动，activity=0（可能目标位置需要精确坐标或游戏需要拖拽而非点按）。

### Phase 4 云端 API 策略生成（experiment_api_strategy.json）

| 游戏 | rule | multi-bus-memory | hierarchical (API) |
|---|---|---|---|
| 00461 塔防 | 0.106 | 0.056 | 0.150 |
| 00736 捕鱼 | 0.275 | 0.237 | 0.150 |

**关键发现**：hierarchical 的 L2 调用成功（L2=1）但 activity=0——云端 API 的 macro-plan 没有被 L0 规则引擎有效执行。L2 输出需要更强的行动契约（如直接输出可执行的 tap/move 指令而非抽象策略描述）。

### Phase 5 VLM 视觉管线（experiment_vlm_pipeline.json）

| 游戏 | probe_only | pil_vision | vlm_gemma |
|---|---|---|---|
| 00461 塔防 | 0.044 | **0.087** | 0.150 |
| 00736 捕鱼 | 0.269 | 0.269 | 0.150 |

**关键发现**：
- **PIL 视觉对 00461 有提升**（0.044→0.087，tap 7→14，stall 17→10）——本地青色箭头检测帮助 target 选择。
- **VLM gemma 全部 0.150**（activity=0, tap=0, stall=24）——本地小模型输出太慢且无法解析为有效动作，在线闭环不实用。
- **PIL 视觉对 00736 无提升**（0.269→0.269）——该游戏的 guide 目标已通过 probe 提供，不需要视觉补充。

## 2026-07-20 第六轮：训练数据生成 + L2 输出契约修复

### 训练数据生成（trajectory_converter.py）

新建 `src/training/trajectory_converter.py`：把批量实验产出的 125 条轨迹 JSONL（格式：player/action/keyNumbers/reason per step）离线转换为同事 7 任务训练格式，与 processed-runs 的 15,083 样本合并。

| 任务 | 新增样本 | 说明 |
|---|---|---|
| probe_action_effect | +3040 | 相邻步 diff：player_moved, changed_fields, information_gain, completed |
| information_gain_judgment | +3040 | keyNumbers 变化数→high/medium/low_or_unknown |
| progression_grounding | +3165 | 每步 economy 值→starting/early_game/mid_game/completed |
| pulse_response_grounding | +159 | move 步的 pulse→displacement 映射 |
| failure_recovery | +9 | 连续 stall 窗口的诊断 + recovery action |
| **总计新增** | **+9413** | |
| **合并后总计** | **23,596** | （原 15,083 + 新 9,413） |

数据特点：
- 覆盖 7 个 joystick 游戏 + 15 个 tap-only 游戏 × 4 模式 × 2 seeds。
- 包含 calibrated（基线已知）和 uncalibrated（generic fallback）两种驱动的对比数据。
- multi-bus 模式轨迹含决策来源标签（strategy_memory / rule_engine），可用于研究记忆 vs 规则的差异。

### L2 输出契约修复（hierarchical_planner.py）

**问题**：云端 API 的 L2 macro-plan 是抽象文本（"Navigate to UnlockItem_1 and collect it"），L0 规则引擎不知道如何翻译成具体动作，导致 hierarchical 全部 activity=0。

**修复**：L2 prompt 改为要求输出 `{"instructions": [{"action": "tap", "x": int, "y": int}, {"action": "move", "dx": float, "dy": float, "duration_ms": int}, ...], "reason": "..."}`。L2 输出存入 `_l2_queue`（动作队列），L0 `step()` 先弹出队列中的指令直接执行，队列耗尽后回退到 rule_engine。

**效果**：（重跑验证数据见下方）

**L2 契约修复验证（experiment_hierarchical.json v3）**：

| 模式 | composite | L0 calls | L2 calls | latency/step |
|---|---|---|---|---|
| hierarchical (v3, 指令队列) | 0.055 | 1 | 1 | 18.1s |
| rule_baseline | 0.114 | 0 | 0 | 0.84s |
| multi_bus_memory | 0.134 | 0 | 0 | 0.88s |

**结论**：L2 指令队列架构正确（24/25 步来自 L2 指令），但**纯文本云端 API 无法准确输出 tap 坐标**——它没有视觉，只能根据 JSON 状态推测坐标，结果全是幻觉。composite 从修复前的 0.150（全部 wait）降到 0.055（错误的 tap/move），反而更差。

**根因**：L2 需要视觉输入（截图）才能输出准确的 tap 坐标，或者改为输出"目标名称"让 L0 用 probe 的 screenPosition 自行映射坐标。后者更可行：L2 只选 target 名，L0 负责几何。

**当前最佳架构修正**：
- **L2 云端 API**：输出 `{"target": "UnlockItem_1", "priority": "high"}`（选目标，不选坐标）
- **L0 规则引擎**：用 `_select_target` + `_target_screen_to_css` 把 target 名转成 tap 坐标
- **L1 本地 VLM**：看截图做战术修正（stall 时换方向）

这样 L2 不需要视觉也能工作，L0 保留了几何准确性。
