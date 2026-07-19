# Batch Experiment Analysis

## Per-Mode Summary

| Mode | Runs | Mean Composite | Mean Activity | Mean Latency (s) | Errors |
|---|---|---|---|---|---|
| multi-bus-memory | 5 | 0.393 | 0.750 | 20.3 | 0 |
| rule | 5 | 0.386 | 0.708 | 20.9 | 0 |

## Per-Game Breakdown

### SSD_00342P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 1 | 0.700 | 1.000 | 10.8s | 0 | 0 | 0 |
| rule | 7 | 1 | 0.700 | 1.000 | 11.0s | 0 | 0 | 0 |
### SSD_00461P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 25 | 0.131 | 0.875 | 21.8s | 21 | 3 | 3 |
| rule | 7 | 25 | 0.112 | 0.750 | 25.3s | 18 | 6 | 6 |
### SSD_00482P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 1 | 0.700 | 1.000 | 10.6s | 0 | 0 | 0 |
| rule | 7 | 1 | 0.700 | 1.000 | 10.6s | 0 | 0 | 0 |
### SSD_00532P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 25 | 0.150 | 0.000 | 32.3s | 0 | 25 | 24 |
| rule | 7 | 25 | 0.150 | 0.000 | 32.8s | 0 | 25 | 24 |
### SSD_00736P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 25 | 0.281 | 0.875 | 26.0s | 22 | 3 | 3 |
| rule | 7 | 25 | 0.269 | 0.792 | 25.0s | 20 | 5 | 5 |

## Trajectory Data

10 runs produced trajectory JSONL files.

| File | Steps |
|---|---|
| `SSD_00461P01_rule_seed7.jsonl` | 25 |
| `SSD_00461P01_multi-bus-memory_seed7.jsonl` | 25 |
| `SSD_00482P01_rule_seed7.jsonl` | 1 |
| `SSD_00482P01_multi-bus-memory_seed7.jsonl` | 1 |
| `SSD_00736P01_rule_seed7.jsonl` | 25 |
| `SSD_00736P01_multi-bus-memory_seed7.jsonl` | 25 |
| `SSD_00342P01_rule_seed7.jsonl` | 1 |
| `SSD_00342P01_multi-bus-memory_seed7.jsonl` | 1 |
| `SSD_00532P01_rule_seed7.jsonl` | 25 |
| `SSD_00532P01_multi-bus-memory_seed7.jsonl` | 25 |
