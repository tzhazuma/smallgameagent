# Batch Experiment Analysis

## Per-Mode Summary

| Mode | Runs | Mean Composite | Mean Activity | Mean Latency (s) | Errors |
|---|---|---|---|---|---|
| multi-bus | 3 | 0.250 | 0.667 | 16.8 | 0 |
| multi-bus-memory | 6 | 0.275 | 0.833 | 15.3 | 0 |
| rule | 6 | 0.239 | 0.861 | 16.8 | 0 |

## Per-Game Breakdown

### SSD_00382P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 42 | 25 | 0.300 | 1.000 | 12.8s | 25 | 0 | 0 |
| rule | 42 | 25 | 0.287 | 0.917 | 14.6s | 22 | 0 | 2 |
### SSD_00461P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus | 42 | 25 | 0.300 | 1.000 | 11.7s | 24 | 0 | 0 |
| multi-bus-memory | 42 | 25 | 0.300 | 1.000 | 11.5s | 24 | 0 | 0 |
| rule | 42 | 25 | 0.149 | 0.792 | 14.0s | 19 | 5 | 5 |
### SSD_00483P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus | 42 | 25 | 0.150 | 0.000 | 23.4s | 0 | 25 | 24 |
| multi-bus-memory | 42 | 25 | 0.150 | 0.000 | 19.8s | 0 | 25 | 24 |
| rule | 42 | 25 | 0.184 | 0.625 | 20.6s | 15 | 10 | 9 |
### SSD_00522P02

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus | 42 | 25 | 0.300 | 1.000 | 15.2s | 24 | 1 | 0 |
| multi-bus-memory | 42 | 25 | 0.300 | 1.000 | 14.9s | 24 | 1 | 0 |
| rule | 42 | 25 | 0.215 | 0.833 | 16.0s | 20 | 5 | 4 |
### SSD_00594P02

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 42 | 25 | 0.300 | 1.000 | 17.3s | 25 | 0 | 0 |
| rule | 42 | 25 | 0.300 | 1.000 | 19.8s | 25 | 0 | 0 |
### SSD_00742P01

| Mode | Seed | Steps | Composite | Activity | Latency | Tap | Move | Stall |
|---|---|---|---|---|---|---|---|---|
| multi-bus-memory | 42 | 25 | 0.300 | 1.000 | 15.7s | 25 | 0 | 0 |
| rule | 42 | 25 | 0.300 | 1.000 | 15.9s | 25 | 0 | 0 |

## Trajectory Data

15 runs produced trajectory JSONL files.

| File | Steps |
|---|---|
| `SSD_00461P01_rule_seed42.jsonl` | 25 |
| `SSD_00461P01_multi-bus_seed42.jsonl` | 25 |
| `SSD_00461P01_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00483P01_rule_seed42.jsonl` | 25 |
| `SSD_00483P01_multi-bus_seed42.jsonl` | 25 |
| `SSD_00483P01_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00522P02_rule_seed42.jsonl` | 25 |
| `SSD_00522P02_multi-bus_seed42.jsonl` | 25 |
| `SSD_00522P02_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00382P01_rule_seed42.jsonl` | 25 |
| `SSD_00382P01_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00594P02_rule_seed42.jsonl` | 25 |
| `SSD_00594P02_multi-bus-memory_seed42.jsonl` | 25 |
| `SSD_00742P01_rule_seed42.jsonl` | 25 |
| `SSD_00742P01_multi-bus-memory_seed42.jsonl` | 25 |
