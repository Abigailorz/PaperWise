"""模型配置"""

from dataclasses import dataclass, field
from typing import Optional, Literal


@dataclass
class ModelConfig:
    """单个 LLM 模型的配置"""
    provider: str = "deepseek"       # deepseek | moonshot | openai | openai_compatible
    model_id: str = "deepseek-chat"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    max_tokens: int = 4096
    temperature: float = 0.3
    thinking: bool = False           # DeepSeek/Kimi thinking mode
