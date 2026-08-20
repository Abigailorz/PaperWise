# PaperWise Ablation Study Report

Generated: 2026-08-19 10:47:03
Model: deepseek-v4-flash
k: 1

## Configurations

| Config | Description |
|--------|-------------|
| full | Plan + Budget + Judge + HierarchicalMemory |
| no-plan | Remove explicit Plan |
| no-budget | Remove budget-aware guidance |
| no-judge | Remove Judge review |
| no-memory | Remove HierarchicalMemory compression |
| baseline | Basic ReAct without plan/budget/judge/memory |

## Paper: feature3dgs_2312.03203

| Config | Success Rate | Pass@k | Pass^k | Avg Steps | Avg Tokens | Avg Duration | Significant vs Baseline |
|--------|--------------|--------|--------|-----------|------------|--------------|-------------------------|
| full | 16.7% | 16.7% | 16.7% | 9.8 | 4282 | 70.2s | No |
| no-plan | 16.7% | 16.7% | 16.7% | 11.2 | 7233 | 80.0s | No |
| no-budget | 16.7% | 16.7% | 16.7% | 4.7 | 1389 | 29.4s | No |
| no-judge | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.4s | No |
| no-memory | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.3s | No |
| baseline | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.3s | No |

### Differences vs Baseline

| Config | Success Rate Δ | Avg Steps Δ | Avg Tokens Δ |
|--------|----------------|-------------|--------------|
| full | +16.7% | +9.8 | +4282 |
| no-plan | +16.7% | +11.2 | +7233 |
| no-budget | +16.7% | +4.7 | +1389 |
| no-judge | +0.0% | +0.0 | +0 |
| no-memory | +0.0% | +0.0 | +0 |

## Paper: langsplat_2312.16084

| Config | Success Rate | Pass@k | Pass^k | Avg Steps | Avg Tokens | Avg Duration | Significant vs Baseline |
|--------|--------------|--------|--------|-----------|------------|--------------|-------------------------|
| full | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.4s | No |
| no-plan | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.4s | No |
| no-budget | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.6s | No |
| no-judge | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.5s | No |
| no-memory | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.5s | No |
| baseline | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.5s | No |

### Differences vs Baseline

| Config | Success Rate Δ | Avg Steps Δ | Avg Tokens Δ |
|--------|----------------|-------------|--------------|
| full | +0.0% | +0.0 | +0 |
| no-plan | +0.0% | +0.0 | +0 |
| no-budget | +0.0% | +0.0 | +0 |
| no-judge | +0.0% | +0.0 | +0 |
| no-memory | +0.0% | +0.0 | +0 |

## Paper: gaussaingrouping_2312.00732

| Config | Success Rate | Pass@k | Pass^k | Avg Steps | Avg Tokens | Avg Duration | Significant vs Baseline |
|--------|--------------|--------|--------|-----------|------------|--------------|-------------------------|
| full | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.3s | No |
| no-plan | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.4s | No |
| no-budget | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.4s | No |
| no-judge | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.3s | No |
| no-memory | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.4s | No |
| baseline | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.3s | No |

### Differences vs Baseline

| Config | Success Rate Δ | Avg Steps Δ | Avg Tokens Δ |
|--------|----------------|-------------|--------------|
| full | +0.0% | +0.0 | +0 |
| no-plan | +0.0% | +0.0 | +0 |
| no-budget | +0.0% | +0.0 | +0 |
| no-judge | +0.0% | +0.0 | +0 |
| no-memory | +0.0% | +0.0 | +0 |

## Paper: mipsplatting_2311.16493

| Config | Success Rate | Pass@k | Pass^k | Avg Steps | Avg Tokens | Avg Duration | Significant vs Baseline |
|--------|--------------|--------|--------|-----------|------------|--------------|-------------------------|
| full | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.5s | No |
| no-plan | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.5s | No |
| no-budget | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.5s | No |
| no-judge | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.4s | No |
| no-memory | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.3s | No |
| baseline | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.3s | No |

### Differences vs Baseline

| Config | Success Rate Δ | Avg Steps Δ | Avg Tokens Δ |
|--------|----------------|-------------|--------------|
| full | +0.0% | +0.0 | +0 |
| no-plan | +0.0% | +0.0 | +0 |
| no-budget | +0.0% | +0.0 | +0 |
| no-judge | +0.0% | +0.0 | +0 |
| no-memory | +0.0% | +0.0 | +0 |

## Paper: gaussianeditor_2311.14521

| Config | Success Rate | Pass@k | Pass^k | Avg Steps | Avg Tokens | Avg Duration | Significant vs Baseline |
|--------|--------------|--------|--------|-----------|------------|--------------|-------------------------|
| full | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.4s | No |
| no-plan | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.5s | No |
| no-budget | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.3s | No |
| no-judge | 0.0% | 0.0% | 0.0% | 0.0 | 0 | 1.3s | No |
| no-memory | 16.7% | 16.7% | 16.7% | 7.5 | 5667 | 56.3s | No |
| baseline | 0.0% | 0.0% | 0.0% | 10.7 | 7462 | 75.3s | No |

### Differences vs Baseline

| Config | Success Rate Δ | Avg Steps Δ | Avg Tokens Δ |
|--------|----------------|-------------|--------------|
| full | +0.0% | -10.7 | -7462 |
| no-plan | +0.0% | -10.7 | -7462 |
| no-budget | +0.0% | -10.7 | -7462 |
| no-judge | +0.0% | -10.7 | -7462 |
| no-memory | +16.7% | -3.2 | -1795 |
