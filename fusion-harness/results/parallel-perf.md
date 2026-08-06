# 并行性能对比（Phase 4 实测）

## Edge 复用 vs 每游戏重启

| 模式 | 2 游戏总耗时 | 说明 |
|---|---|---|
| **reuse**（单 Edge 实例，顺序 context） | **487s** | tiles + kingshot-0042aa，无重启开销，无白屏风险 |
| restart（每游戏 taskkill + 重启 Edge） | 487s + N×~15s | 每游戏额外 ~15s（taskkill 2s + Edge 启动 8s + 稳定 5s）；且重启有端口 TIME_WAIT 白屏风险（此前 batch 观察到） |

## 效率优化工具（fusion/browser_eff.py）

1. **截图压缩**：JPEG q70 + 750px 上限。真实游戏截图 **2.67MB PNG → 62KB JPEG（-97.7%）**
   - 每帧传输/内存从 ~2.7MB 降到 ~62KB（VLM 调用 base64 也小 40 倍）
2. **探针节流**（adaptive evidence budget）：
   - required 原因（run_boundary/terminal/stage_boundary/visual_model_input）必采
   - 可选截图受 count(48)/bytes(12MB)/gap(3步)/scene-unchanged 四重抑制
3. **Edge 复用**：单实例多 context（无需每游戏重启）

## 结论
- Edge 复用（reuse）在并行/批量场景减少每游戏 ~15s 启动开销 + 消除白屏风险，487s/2 游戏（平均 4 分钟/游戏，其中 mimo 规划占大头）。
- 截图压缩 + 探针节流进一步降低 CPU/内存/传输，直接缓解"并行运行卡顿"。
- 4 worker 线程并行测试 0.2s（无共享状态任务），真实游戏并行建议 2-3 个（Edge 单实例 + mimo 限流）。
