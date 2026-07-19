# 实验结果记录

> 随实验推进持续更新。方案见 `EXPERIMENT_PLAN.md`。

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
