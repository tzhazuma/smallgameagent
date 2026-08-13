# 云端任务平台运行报告（A_ 系列全量终态）

**仓库**: `fps-research/game-agent-harness` @ `zhihao-test` 分支
**模式**: `no_vlm_codex_session`（确定性 harness + Codex 长程规划，无 VLM）
**模型**: gpt-5.6-luna / effort=xhigh
**平台**: http://34.24.205.23:4097/tasks
**报告时间**: 2026-08-13（全部任务终态）

---

## 1. 实验目标

在统一框架（game-agent-harness zhihao-test 分支）下，对精选 HTML5 游戏（whiteout-survival / kingshot）运行自主通关任务，验证：
1. **框架链路完整性**：首跑 → 通关 → 独立复现 → 稳定性验证 → source capsule 沉淀
2. **安全机制有效性**：确定性动作审批链、探针、evaluator、acceptance gate
3. **跨游戏泛化能力**：同一框架 + Codex 长程规划应对不同 Cocos 游戏的交互语义

## 2. 任务设计

- 每任务独立工作区 `games/<GAME_ID>/`（不继承旧配置/探针/记忆/策略/runs）
- 强制平台 managed Chromium CDP（禁止自启浏览器 / Xvfb）
- 禁止修改游戏文件、绕过框架控制游戏
- 通关要求：fixed evaluator = SETTLED_COMPLETE + acceptance gate PASS + stage audit PASS + 干净初始状态独立复现
- 汇报要求：实际分支与完整 SHA、game ID、run ID、游戏结果、复现结果、CDP 模式、停止原因、输出路径

## 3. 全量结果

### 3.1 结果总表

| # | 任务 | 游戏 | 结果 | 停止原因 | 关键信息 |
|---|------|------|------|----------|----------|
| 1 | A_01.2 | kingshot 830518bfdad4 | **SETTLED_COMPLETE** ✅ | settled 完成 | 3/3 复现 PASS, gate 3/3 PASS, capsule 已沉淀, 59 FPS |
| 2 | A_01.1 | whiteout 1f1c7b6176fe | ANALYSIS_REQUIRED | no_progress_boundary | 审计 REVISE |
| 3 | A_02.1 | whiteout b44be04989de | BUDGET_EXHAUSTED | budget exhausted | 180 steps, 复现 ANALYSIS_REQUIRED |
| 4 | A_02.2 | whiteout 12ababda99c7 | BLOCKED_UNSAFE | player_dead 硬终止 | 12 步 DEFEAT/TRY AGAIN |
| 5 | A_03.1 | whiteout b47a4f071e9c | ANALYSIS_REQUIRED | no_progress + quarantine | doctor 16/16, test:fast 161/161 |
| 6 | A_03.2 | kingshot 33efef78d709 | BUDGET_EXHAUSTED | budget exhausted | 240/240 steps, gate PASS 但 INCOMPLETE |
| 7 | A_04.1 | kingshot 2653755ff3a0 | BLOCKED_UNSAFE | lose_panel Defeat | 232 步, invariant failure_active |
| 8 | A_04.2 | kingshot 14271ce32d49 | ANALYSIS_REQUIRED | no_progress_boundary | 角色无法通过围栏边界, 58 FPS |
| 9 | A_05.1 | kingshot 0c1911759bcb | 假阳性 SETTLED_COMPLETE | steps:0 判定缺陷 | normalizer 已修复 |
| 10 | A_05.2 | whiteout c47908da8b11 | BLOCKED_UNSAFE | unsafe_terminal | 55 步 Defeat, 59 FPS |
| 11 | A_06.1 | kingshot 481c77501c7f | ANALYSIS_REQUIRED | — | — |
| 12 | A_06.2 | kingshot 0042aa74feb8 | BUDGET_EXHAUSTED | budget exhausted | 含独立复现, 59 FPS |
| 13 | A_10 | whiteout 12ababda99c7 | BLOCKED_UNSAFE | player_dead | 与 A_02.2 重复 URL |

### 3.2 统计

- **通关率: 1/13 (7.7%)**
- 未通关分布：
  - ANALYSIS_REQUIRED ×4（探索边界，Codex 可修订策略后重试）
  - BUDGET_EXHAUSTED ×3（预算耗尽）
  - BLOCKED_UNSAFE ×4（游戏内死亡 Defeat）
  - 假阳性 ×1（判定缺陷，已修复）

## 4. 关键发现

### 4.1 框架链路有效（A_01.2）
kingshot 830518bfdad4 以 SETTLED_COMPLETE 完成，三次 clean replay + 三次 acceptance gate 全部 PASS，产出 source capsule 与稳定策略。证明"确定性 harness + Codex 长程规划"可以在该框架下端到端通关并复现。

### 4.2 假阳性通关（A_05.1）
在 steps:0 处被判 SETTLED_COMPLETE（复用启动 cam_final_pos/CTA 候选，零动作）。`initialBaselineResidualGameplayContradiction` 未拦住。已在游戏 normalizer 层修复。与 smallgameagent 的 0cee208d789c 假阳性同源——潜在框架改进点（需谨慎，涉及核心判定）。

### 4.3 未通关的共同瓶颈
- **whiteout-survival**（4 任务，3 个 BLOCKED_UNSAFE）：生存压力（Defeat 面板），需要更早期防御策略；死亡通常在 12-55 步内发生，动作空间需要更快进入安全区
- **kingshot**（8 任务）：以 ANALYSIS_REQUIRED / BUDGET_EXHAUSTED 为主，交互语义探索（guide 引导、资源目标验证）是主要瓶颈；一个卡在围栏边界（A_04.2），一个 Defeat（A_04.1）
- 普遍模式：首轮撞 ANALYSIS_REQUIRED 安全边界 → Codex 审图修订策略 → fresh run 重试

### 4.4 性能
所有任务实测 58-59 FPS（NVIDIA L4，ANGLE），headful 真实游戏场景视觉正常，确定性安全监视器全程工作（player_dead / lose_panel 检测可靠）。

## 5. 平台与基础设施

- 平台：http://34.24.205.23:4097/tasks（一次最多 6 个并发任务）
- 容量控制：白天（08:00-19:59）A_ 任务 ≤3；夜间满额 6
- 监控：每 23 分钟快照 → `fusion-harness/results/tasks-snapshots.jsonl` + `tasks-status.md`
- 平台曾两次重启清除排队任务（orphaned），已三次重建 .2 系列
- /tmp cron 脚本曾被清理一次，已重建

## 5.5 A_11 系列（第二轮，HEAD 2e83e20）

| 任务 | 游戏 | 结果 | 备注 |
|------|------|------|------|
| A_11.1 | whiteout 9accaeba2a28 | running | — |
| A_11.2 | whiteout 87790941fd83 | running | — |
| A_11.3 | whiteout e9975db003e7 | **BLOCKED_UNSAFE** | 10 步后 TRY AGAIN 界面，59 FPS |
| A_11.4 | kingshot 94766e5d61dc | running | — |
| A_11.5 | kingshot c378f843e877 | **BLOCKED_UNSAFE** | 9 动作后检测 retry 失败界面，gate STOP，SHA 2e83e20 |
| A_11.6 | kingshot ce59e2a9a7a3 | running | — |

## 6. 结论与建议

### 6.1 结论
1. 融合框架的确定性执行链 + Codex 长程规划模式在部分游戏上可以端到端通关并稳定复现（kingshot 1/8，7.7% 总通关率）
2. whiteout-survival 生存压力是当前最大挑战，需在策略层加入防御性先验（快速撤离/抱团）
3. 假阳性判定缺陷（steps:0 通关）已在 normalizer 层修复，建议评估回灌共享分支

### 6.2 后续建议
1. **whiteout 专项策略**：为 whiteout-survival 注入早期生存先验（开局 30 秒内建立安全区），减少 BLOCKED_UNSAFE
2. **kingshot 语义探索增强**：guide 引导 + 资源目标的语义理解，考虑加 VLM 视觉辅助
3. **假阳性防护回灌**：将 normalizer 修复合并到 zhihao-test 共享分支（需与维护者确认）
4. **A_10 去重**：后续创建任务时校验 URL 唯一性
5. **扩大实验**：当前仅验证 gpt-5.6-luna，可扩展 deepseek-v4-flash / mimo-v2.5 / kimi-k2.7 做模型对比

---
*监控脚本自动生成 + 人工补全。*
