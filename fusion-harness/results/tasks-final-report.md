# 云端任务平台运行报告（A_ 系列）

**仓库**: `fps-research/game-agent-harness` @ `zhihao-test` 分支
**模式**: `no_vlm_codex_session`（确定性 harness + Codex 长程规划，无 VLM）
**模型**: gpt-5.6-luna / effort=xhigh
**报告时间**: 2026-08-13（持续更新中）

---

## 1. 实验目标

在统一框架（game-agent-harness zhihao-test 分支）下，对 12 个精选 HTML5 游戏（whiteout-survival / kingshot）运行自主通关任务，验证：
1. **框架链路完整性**：首跑 → 通关 → 独立复现 → 稳定性验证 → source capsule 沉淀
2. **安全机制有效性**：确定性动作审批链、探针、evaluator、acceptance gate
3. **跨游戏泛化能力**：同一框架 + Codex 长程规划应对不同 Cocos 游戏的交互语义

## 2. 任务设计

- 每任务独立工作区 `games/<GAME_ID>/`（不继承旧配置/探针/记忆/策略/runs）
- 强制平台 managed Chromium CDP（禁止自启浏览器 / Xvfb）
- 禁止修改游戏文件、绕过框架控制
- 通关要求：fixed evaluator = SETTLED_COMPLETE + acceptance gate PASS + stage audit PASS + 干净初始状态独立复现
- 汇报要求：实际分支与完整 SHA、game ID、run ID、游戏结果、复现结果、CDP 模式、停止原因、输出路径

## 3. 已完成任务结果

| # | 任务 | 游戏 | 结果 | 复现 | 备注 |
|---|------|------|------|------|------|
| 1 | A_01.2 | kingshot 830518bfdad4 | **SETTLED_COMPLETE** ✅ | 3/3 PASS | 首个通关；source capsule 已沉淀；59 FPS |
| 2 | A_01.1 | whiteout 1f1c7b6176fe | ANALYSIS_REQUIRED | — | no_progress_boundary，审计 REVISE |
| 3 | A_02.1 | whiteout b44be04989de | BUDGET_EXHAUSTED | 1× (ANALYSIS_REQUIRED) | 180 gameplay steps |
| 4 | A_05.1 | kingshot 0c1911759bcb | 假阳性 SETTLED_COMPLETE | — | steps:0 判定缺陷，normalizer 已修复 |
| 5 | A_05.2 | whiteout c47908da8b11 | BLOCKED_UNSAFE | — | lose_panel/Defeat，55 步，59 FPS |
| 6 | A_06.1 | kingshot 481c77501c7f | ANALYSIS_REQUIRED | — | — |
| 7 | A_06.2 | kingshot 0042aa74feb8 | BUDGET_EXHAUSTED | 1× (ANALYSIS_REQUIRED) | explore audit 通过，59 FPS |
| 8 | A_04.2 | kingshot 14271ce32d49 | ANALYSIS_REQUIRED | — | no_progress_boundary：角色无法通过围栏边界，58 FPS |
| 9 | A_02.2 | whiteout 12ababda99c7 | BLOCKED_UNSAFE | — | 12 步玩家死亡 DEFEAT，硬安全终止 |

**（待补全：A_03.1 / A_03.2 / A_04.1 / A_10 运行中）**

## 4. 运行中任务（截至报告时刻）

| 任务 | 游戏 | 阶段 |
|------|------|------|
| A_02.2 | whiteout 12ababda99c7 | ANALYSIS_REQUIRED 迭代（门碰撞边界分析） |
| A_03.1 | kingshot ? | ANALYSIS_REQUIRED 迭代 |
| A_03.2 | kingshot 33efef78d709 | 首轮 autonomous |
| A_04.1 | kingshot 14271ce32d49 | ANALYSIS_REQUIRED 迭代（资源路线探索） |
| A_04.2 | kingshot ? | 初始化阶段 |
| A_10 | ? | 初始化阶段 |

## 5. 关键发现

### 5.1 首个通关证明链路有效
A_01.2（kingshot）以 SETTLED_COMPLETE 完成，三次 clean replay + 三次 acceptance gate 全部 PASS，产出 source capsule 与稳定策略。证明"确定性 harness + Codex 长程规划"可以在该框架下端到端通关并复现。

### 5.2 假阳性通关（A_05.1）
在 steps:0 处被判 SETTLED_COMPLETE（复用启动 cam_final_pos/CTA 候选，零动作）。`initialBaselineResidualGameplayContradiction` 未拦住。已在游戏 normalizer 层修复。与我们 smallgameagent 的 0cee208d789c 假阳性同源——潜在框架改进点（需谨慎，涉及核心判定）。

### 5.3 未通关的共同瓶颈
- **whiteout-survival**：生存压力（Defeat 面板）、无进展边界
- **kingshot**：交互语义探索（guide 引导、资源目标不可验证）、预算耗尽
- 普遍模式：首轮撞 ANALYSIS_REQUIRED 安全边界 → Codex 审图修订策略 → fresh run 重试

### 5.4 性能
所有任务实测 59 FPS 左右（NVIDIA L4，ANGLE），headful 真实游戏场景视觉正常。

## 6. 平台与基础设施

- 平台：http://34.24.205.23:4097/tasks（一次最多 6 个并发任务）
- 容量控制：白天（08:00-19:59）我的 A_ 任务 ≤3；夜间可满额 6
- 监控：每 23 分钟快照 → `fusion-harness/results/tasks-snapshots.jsonl` + `tasks-status.md`
- 容量控制日志：`fusion-harness/results/capacity-control.jsonl`
- 平台曾两次重启清除排队任务（orphaned），已三次重建 .2 系列

## 7. 结论与建议（待终态后补全）

---
*由监控脚本自动生成骨架，人工补全细节后推送。*
