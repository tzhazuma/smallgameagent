# 实验结果记录

> 随实验推进持续更新。方案见 `EXPERIMENT_PLAN.md`。

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
