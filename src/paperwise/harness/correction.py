"""纠正机制 — 重试逻辑与熔断器

对应书中 1.2 节：纠正机制
- 静默重试：API 超时先静默重试，不暴露中间态
- 熔断器：连续失败超过阈值时触发
"""

from typing import Optional


class Corrector:
    """纠正管理器。

    策略：
    - 可重试错误：网络超时、临时 API 错误 → 指数退避重试
    - 不可重试错误：参数错误、权限拒绝 → 直接报告
    - 熔断器：连续 5 次错误 → 停止并交还人工
    """

    RETRYABLE_ERRORS = [
        "timeout", "timed out", "rate_limit", "rate limit",
        "server_error", "service_unavailable", "connection",
        "try again", "temporary",
    ]

    MAX_RETRIES = 3
    CIRCUIT_BREAKER_THRESHOLD = 5

    def __init__(self):
        self.consecutive_errors = 0
        self.max_retries = self.MAX_RETRIES
        self.circuit_threshold = self.CIRCUIT_BREAKER_THRESHOLD

    def should_retry(self, error: Exception, attempt: int) -> bool:
        """判断是否应该重试。

        Args:
            error: 发生的异常
            attempt: 当前尝试次数（从 1 开始）

        Returns:
            True 如果应该重试
        """
        if attempt >= self.max_retries:
            return False

        error_str = str(error).lower()
        return any(pattern in error_str for pattern in self.RETRYABLE_ERRORS)

    def backoff_delay(self, attempt: int) -> float:
        """计算指数退避延迟（秒）。"""
        return min(2 ** attempt, 30)  # 2, 4, 8, 16, 30 秒上限

    def record_error(self) -> None:
        """记录一次错误。返回是否触发熔断器。"""
        self.consecutive_errors += 1

    def record_success(self) -> None:
        """记录一次成功，重置连续错误计数。"""
        self.consecutive_errors = 0

    def is_circuit_open(self) -> bool:
        """检查熔断器是否触发。"""
        return self.consecutive_errors >= self.circuit_threshold

    def reset(self) -> None:
        """重置熔断器。"""
        self.consecutive_errors = 0
