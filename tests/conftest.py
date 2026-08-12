"""共享测试 fixtures"""

import pytest
from pathlib import Path

from fastapi.testclient import TestClient


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """创建临时 workspace 目录。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def sample_text() -> str:
    """测试用论文文本。"""
    return """# Attention Is All You Need

## Abstract
The dominant sequence transduction models are based on complex recurrent
or convolutional neural networks. We propose a new simple network architecture,
the Transformer, based solely on attention mechanisms.

## 1 Introduction
Recurrent neural networks have been the state-of-the-art approach
for sequence modeling and transduction problems.

## 2 Background
The goal of reducing sequential computation also forms the foundation
of the Extended Neural GPU.

## 3 Model Architecture
Most competitive neural sequence transduction models have an
encoder-decoder structure.

### 3.1 Encoder and Decoder Stacks
Encoder: The encoder is composed of a stack of N = 6 identical layers.

### 3.2 Attention
An attention function can be described as mapping a query and a set
of key-value pairs to an output.

#### 3.2.1 Scaled Dot-Product Attention
The input consists of queries and keys of dimension dk, and values
of dimension dv.

$$\\text{Attention}(Q, K, V) = \\text{softmax}(\\frac{QK^T}{\\sqrt{d_k}})V$$

#### 3.2.2 Multi-Head Attention
Instead of performing a single attention function with dmodel-dimensional
keys, values and queries, we found it beneficial to linearly project
the queries, keys and values h times.

## 4 Why Self-Attention
Comparing self-attention layers to recurrent and convolutional layers.

## 5 Training
### 5.1 Training Data and Batching
We trained on the standard WMT 2014 English-German dataset.

### 5.2 Hardware and Schedule
We trained our models on one machine with 8 NVIDIA P100 GPUs.

### 5.3 Optimizer
We used the Adam optimizer with beta1 = 0.9, beta2 = 0.98.

### 5.4 Regularization
We employ three types of regularization during training: Residual Dropout,
Label Smoothing.

## 6 Results
### 6.1 Machine Translation
On the WMT 2014 English-to-German translation task, the big transformer
model outperforms the best previously reported models by more than 2.0 BLEU.

### 6.2 Model Variations
To evaluate the importance of different components, we varied our base model.

## 7 Conclusion
In this work, we presented the Transformer, the first sequence transduction
model based entirely on attention.

## References
[1] Bahdanau et al. Neural Machine Translation by Jointly Learning to Align and Translate. 2014.
[2] Vaswani et al. Attention Is All You Need. 2017.
"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """FastAPI TestClient，工作区指向临时目录（环境变量优先于 .env）。"""
    import paperwise.config.settings as settings_mod
    import paperwise.core.scheduler as scheduler_mod
    from paperwise.api.server import app
    ws = tmp_path / "ws"
    monkeypatch.setenv("PAPERWISE_WORKSPACE", str(ws))
    settings_mod._settings = None  # 强制重新加载
    # 每个测试独立的调度器实例（避免跨事件循环的 stop 挂起）
    scheduler_mod.Scheduler._instance = None
    return TestClient(app)
