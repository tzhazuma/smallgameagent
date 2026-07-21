# Batch Experiment Analysis

## Per-Mode Summary

| Mode | Runs | Mean Composite | Mean Activity | Mean Latency (s) | Errors |
|---|---|---|---|---|---|
| multi-bus-memory | 3 | 0.296 | 0.972 | 15.3 | 0 |
| rule | 3 | 0.300 | 1.000 | 15.5 | 0 |

## Per-Game Breakdown

### SSD_00382P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 42 | 25 | 0.287 | 0.917 | 14.0s | 22 | 0 | 2 |
| rule | 42 | 25 | 0.300 | 1.000 | 12.9s | 25 | 0 | 0 |
### SSD_00594P02

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 42 | 25 | 0.300 | 1.000 | 16.8s | 25 | 0 | 0 |
| rule | 42 | 25 | 0.300 | 1.000 | 17.7s | 25 | 0 | 0 |
### SSD_00742P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 42 | 25 | 0.300 | 1.000 | 15.2s | 25 | 0 | 0 |
| rule | 42 | 25 | 0.300 | 1.000 | 15.8s | 25 | 0 | 0 |

## Trajectory Data

6 runs produced trajectory JSONL files.

| File | Steps |
|---|---|
| `SSD_00382P01_rule_seed42.jsonl` | 25 |
| `SSD_00594P02_rule_seed42.jsonl` | 25 |
| `SSD_00742P01_rule_seed42.jsonl` | 25 |
| `SSD_00382P01_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00594P02_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00742P01_multi-bus-memory_seed42.jsonl` | 25 |
