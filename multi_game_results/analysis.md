# Batch Experiment Analysis

## Per-Mode Summary

| Mode | Runs | Mean Composite | Mean Activity | Mean Latency (s) | Errors |
|---|---|---|---|---|---|
| multi-bus-memory | 5 | 0.210 | 0.400 | 27.7 | 0 |
| rule | 5 | 0.175 | 0.325 | 29.0 | 0 |

## Per-Game Breakdown

### SSD_00342P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 25 | 0.150 | 0.000 | 31.3s | 0 | 0 | 24 |
| rule | 7 | 25 | 0.150 | 0.000 | 32.4s | 0 | 0 | 24 |
### SSD_00461P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 25 | 0.300 | 1.000 | 18.5s | 24 | 0 | 0 |
| rule | 7 | 25 | 0.155 | 0.833 | 20.5s | 20 | 4 | 4 |
### SSD_00482P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 25 | 0.150 | 0.000 | 33.2s | 0 | 0 | 24 |
| rule | 7 | 25 | 0.150 | 0.000 | 33.2s | 0 | 0 | 24 |
### SSD_00532P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 25 | 0.150 | 0.000 | 32.1s | 0 | 25 | 24 |
| rule | 7 | 25 | 0.150 | 0.000 | 33.9s | 0 | 25 | 24 |
### SSD_00736P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 7 | 25 | 0.300 | 1.000 | 23.6s | 25 | 0 | 0 |
| rule | 7 | 25 | 0.269 | 0.792 | 24.8s | 20 | 5 | 5 |

## Trajectory Data

10 runs produced trajectory JSONL files.

| File | Steps |
|---|---|
| `SSD_00461P01_rule_seed7.jsonl` | 25 |
| `SSD_00461P01_multi-bus-memory_seed7.jsonl` | 25 |
| `SSD_00482P01_rule_seed7.jsonl` | 25 |
| `SSD_00482P01_multi-bus-memory_seed7.jsonl` | 25 |
| `SSD_00736P01_rule_seed7.jsonl` | 25 |
| `SSD_00736P01_multi-bus-memory_seed7.jsonl` | 25 |
| `SSD_00342P01_rule_seed7.jsonl` | 25 |
| `SSD_00342P01_multi-bus-memory_seed7.jsonl` | 25 |
| `SSD_00532P01_rule_seed7.jsonl` | 25 |
| `SSD_00532P01_multi-bus-memory_seed7.jsonl` | 25 |
