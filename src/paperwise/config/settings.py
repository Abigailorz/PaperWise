"""配置管理 — 基于 pydantic-settings，从 .env 和环境变量加载"""

import os
from pathlib import Path
from typing import Optional, Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator


class Settings(BaseSettings):
    """PaperWise 全局设置"""

    # === LLM Provider ===
    llm_provider: Literal["deepseek", "moonshot", "openai", "openai_compatible"] = Field(
        default="deepseek", alias="PAPERWISE_LLM_PROVIDER"
    )
    default_model: str = Field(default="deepseek-chat", alias="PAPERWISE_DEFAULT_MODEL")

    # === Judge（异源评估，独立于主模型，可单独换 provider / key / model / url）===
    judge_provider: Literal["deepseek", "moonshot", "openai", "openai_compatible"] = Field(
        default="deepseek", alias="PAPERWISE_JUDGE_PROVIDER"
    )
    judge_model: str = Field(default="deepseek-chat", alias="PAPERWISE_JUDGE_MODEL")
    judge_api_key: str = Field(default="", alias="PAPERWISE_JUDGE_API_KEY")
    judge_base_url: str = Field(default="", alias="PAPERWISE_JUDGE_BASE_URL")

    # === API Keys ===
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")

    moonshot_api_key: str = Field(default="", alias="MOONSHOT_API_KEY")
    moonshot_base_url: str = Field(default="https://api.moonshot.cn/v1", alias="MOONSHOT_BASE_URL")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")

    # === Embeddings API ===
    embedding_api_key: str = Field(default="", alias="PAPERWISE_EMBEDDING_API_KEY")
    embedding_base_url: str = Field(default="https://api.openai.com/v1", alias="PAPERWISE_EMBEDDING_BASE_URL")
    embedding_model: str = Field(default="text-embedding-3-small", alias="PAPERWISE_EMBEDDING_MODEL")

    # === Workspace ===
    workspace_dir: Path = Field(default=Path("workspace"), alias="PAPERWISE_WORKSPACE")

    # === Agent Defaults ===
    max_steps: int = Field(default=25, alias="PAPERWISE_MAX_STEPS")
    token_budget: int = Field(default=180_000, alias="PAPERWISE_TOKEN_BUDGET")
    cost_budget_usd: float = Field(default=5.0, alias="PAPERWISE_COST_BUDGET_USD")
    context_window: int = Field(default=128_000, alias="PAPERWISE_CONTEXT_WINDOW")
    temperature: float = Field(default=0.3, alias="PAPERWISE_TEMPERATURE")
    time_budget_seconds: int = Field(default=1800, alias="PAPERWISE_TIME_BUDGET")
    early_term_threshold: int = Field(default=2, alias="PAPERWISE_EARLY_TERM_THRESHOLD")
    max_consecutive_errors: int = Field(default=5, alias="PAPERWISE_MAX_CONSECUTIVE_ERRORS")
    max_retries: int = Field(default=3, alias="PAPERWISE_MAX_RETRIES")
    circuit_breaker_threshold: int = Field(default=5, alias="PAPERWISE_CIRCUIT_BREAKER")
    compression_trigger: float = Field(default=0.85, alias="PAPERWISE_COMPRESSION_TRIGGER")
    tool_output_max_chars: int = Field(default=8000, alias="PAPERWISE_TOOL_OUTPUT_MAX_CHARS")
    archive_window: int = Field(default=20, alias="PAPERWISE_ARCHIVE_WINDOW")
    trajectory_max: int = Field(default=100, alias="PAPERWISE_TRAJECTORY_MAX")

    # === RAG ===
    advanced_rag: bool = Field(default=False, alias="PAPERWISE_ADVANCED_RAG")
    # False: 基础 RAG（Dense + BM25 + Rerank）
    # True: 高级 RAG（额外启用 HyDE / RAPTOR / GraphRAG / 上下文感知查询）

    # === Provider Resolution ===
    @property
    def api_key(self) -> str:
        """Get the API key for the active provider."""
        provider_keys = {
            "deepseek": self.deepseek_api_key,
            "moonshot": self.moonshot_api_key,
            "openai": self.openai_api_key,
            "openai_compatible": self.openai_api_key,
        }
        key = provider_keys.get(self.llm_provider, "")
        if not key:
            raise ValueError(f"No API key configured for provider '{self.llm_provider}'")
        return key

    @property
    def base_url(self) -> str:
        """Get the base URL for the active provider."""
        provider_urls = {
            "deepseek": self.deepseek_base_url,
            "moonshot": self.moonshot_base_url,
            "openai": self.openai_base_url,
            "openai_compatible": self.openai_base_url,
        }
        return provider_urls.get(self.llm_provider, self.openai_base_url)

    @property
    def judge_api_key_resolved(self) -> str:
        """Judge API key；未单独配置时回退到对应 provider 的主 key。"""
        if self.judge_api_key:
            return self.judge_api_key
        provider_keys = {
            "deepseek": self.deepseek_api_key,
            "moonshot": self.moonshot_api_key,
            "openai": self.openai_api_key,
            "openai_compatible": self.openai_api_key,
        }
        return provider_keys.get(self.judge_provider, "")

    @property
    def judge_base_url_resolved(self) -> str:
        """Judge base URL；未单独配置时回退到对应 provider 的默认 URL。"""
        if self.judge_base_url:
            return self.judge_base_url
        provider_urls = {
            "deepseek": self.deepseek_base_url,
            "moonshot": self.moonshot_base_url,
            "openai": self.openai_base_url,
            "openai_compatible": self.openai_base_url,
        }
        return provider_urls.get(self.judge_provider, self.openai_base_url)

    def build_judge_llm(self):
        """构建异源 Judge 的 LLMClient（独立于主模型）。"""
        from paperwise.core.llm_client import LLMClient
        return LLMClient(
            provider=self.judge_provider,
            model=self.judge_model,
            api_key=self.judge_api_key_resolved,
            base_url=self.judge_base_url_resolved,
        )

    # === Safety Validation ===
    @model_validator(mode='after')
    def judge_must_be_heterogeneous(self):
        """Judge 不应与主模型完全相同；至少 provider 或 model 不同。"""
        same_provider = self.judge_provider == self.llm_provider
        same_model = self.judge_model == self.default_model
        if same_provider and same_model:
            allowed = os.environ.get("PAPERWISE_ALLOW_HOMOGENEOUS_JUDGE", "0") == "1"
            if not allowed:
                raise ValueError(
                    "Judge model must differ from the main LLM to avoid same-source bias. "
                    "Set a different PAPERWISE_JUDGE_MODEL / PAPERWISE_JUDGE_PROVIDER, "
                    "or set PAPERWISE_ALLOW_HOMOGENEOUS_JUDGE=1 to bypass."
                )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Global settings instance (lazy-loaded)
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get or create the global Settings instance."""
    global _settings
    if _settings is None:
        _warn_if_env_tracked()
        _settings = Settings()
    return _settings



def _warn_if_env_tracked() -> None:
    """如果 .env 被 git 跟踪，打印安全警告。"""
    env_path = Path(".env")
    if not env_path.exists():
        return
    try:
        import subprocess
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(env_path)],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            print(
                "\n[SECURITY WARNING] .env is tracked by git. "
                "API keys must not be committed. Run: git rm --cached .env\n"
            )
    except Exception:
        pass
