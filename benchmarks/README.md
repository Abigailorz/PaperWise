# PaperWise 固定论文基线

本目录用于维护 PaperWise 的固定能力基线，对应 P0 改进项。

## 固定论文

| 论文 | arXiv ID | 金标文件 |
|------|----------|----------|
| 3D Gaussian Splatting | 2308.04079 | `测评/results/golden/golden_3dgs_2308.04079.json` |
| LangSplat | 2312.16084 | `测评/results/golden/golden_langsplat_2312.16084.json` |
| Feature 3DGS | 2312.03203 | `测评/results/golden/golden_feature3dgs_2312.03203.json` |
| Gaussian Grouping | 2312.00732 | `测评/results/golden/golden_gaussaingrouping_2312.00732.json` |

## 下载论文

```bash
python 测评/scripts/download_papers.py
```

PDF 会保存到 `tests/test_data/real_papers/`。

## 跑真实论文测评

```bash
python 测评/scripts/run_real_evaluation.py --paper feature3dgs_2312.03203 --k 1
python 测评/scripts/run_real_evaluation.py --paper langsplat_2312.16084 --k 1
python 测评/scripts/run_real_evaluation.py --paper gaussaingrouping_2312.00732 --k 1
```

结果会落到 `workspace/eval_outputs/`。
