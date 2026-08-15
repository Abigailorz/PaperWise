"""共享安全模块 — 消除 ConstraintEngine 和 BashTool 之间的重复。

单一真相来源（Single Source of Truth）：
所有命令和路径安全检查在此集中定义，消除维护两套规则的风险。
"""

import re
from pathlib import Path


# 危险命令模式（正则，比首词匹配更全面）
DANGEROUS_COMMAND_PATTERNS = [
    r'\brm\s+(-rf?|--recursive)',  r'\bsudo\b',   r'\bchmod\b',
    r'\bchown\b',  r'\bmkfs\b',    r'\bdd\s+if=', r'\bshutdown\b',
    r'\breboot\b', r'\bwget\b.*\|.*sh\b',         r'\bcurl\b.*\|.*sh\b',
    r'>\s*/dev/',  r'\$\(',        r'`[^`]+`',
    r'\bpkexec\b', r'\bmount\s+-', r'\bfdisk\b',
]

# 注入检测模式
INJECTION_PATTERNS = [
    r'<\|im_start\|>', r'<\|im_end\|>',
    r'\[SYSTEM\].*\[/SYSTEM\]',
    r'ignore\s+(?:all\s+|previous\s+|prior\s+|above\s+|the\s+)*instructions',
    r'disregard\s+(?:all\s+|previous\s+|prior\s+|above\s+|the\s+)*instructions',
    r'you are now', r'act as if', r'pretend to be',
    r'\[INST\].*\[/INST\]',
]

# API key 泄露模式
API_KEY_PATTERN = r'sk-[a-zA-Z0-9]{20,}'

# 系统提示词泄露保护
SYSTEM_PROMPT_MARKERS = ['<agent_identity>', '<security_rules>']

# 危险路径模式（第一层防线：所有工具统一拦截）
DANGEROUS_PATH_PATTERNS = [
    # 通用遍历攻击
    r'\.\./\.\.', r'\.\.\\\.\.',
    # Linux 敏感目录
    r'/etc/', r'/proc/', r'/sys/', r'/dev/', r'/boot/',
    # Windows 敏感目录（正反斜杠都覆盖）
    r'[Cc]:[\\/]Windows[\\/]', r'[Cc]:[\\/]Windows$',
    r'[\\/]Windows[\\/]System32[\\/]',
    r'[\\/]Windows[\\/]SysWOW64[\\/]',
    # SSH / 凭证
    r'\.ssh[\\/]', r'\.ssh$',
    r'id_rsa', r'id_ed25519', r'id_ecdsa',
    # 凭证和配置泄露
    r'\.aws[\\/]credentials', r'\.config[\\/]gcloud',
    r'\.gitcredentials', r'\.netrc',
    # Windows 凭证
    r'SAM$', r'SYSTEM$', r'SECURITY$',
    r'NTUSER\.DAT', r'\.rdp$',
    # 浏览器数据
    r'AppData[\\/]Local[\\/](Google|Microsoft|Mozilla|Opera|Brave)',
    r'AppData[\\/]Roaming[\\/](Code|Cursor)[\\/]User[\\/]',
    # 注册表
    r'reg\.exe', r'regedit',
]


def check_command_dangerous(command: str) -> str | None:
    """检查命令是否危险。返回匹配的模式，None 表示安全。"""
    cmd_lower = command.lower()
    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, cmd_lower):
            return pattern
    return None


def check_path_dangerous(path_str: str) -> str | None:
    """检查路径是否危险。返回匹配的模式，None 表示安全。"""
    for pattern in DANGEROUS_PATH_PATTERNS:
        if re.search(pattern, str(path_str)):
            return pattern
    return None


def check_injection(user_input: str) -> bool:
    """检查输入是否包含提示注入模式。"""
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, user_input, re.IGNORECASE):
            return True
    return False


def check_api_key_leak(output: str) -> bool:
    """检查输出是否包含 API key 泄露。"""
    return bool(re.search(API_KEY_PATTERN, output))


def check_system_prompt_leak(output: str) -> bool:
    """检查输出是否包含系统提示词泄露。"""
    return any(marker in output for marker in SYSTEM_PROMPT_MARKERS)


# 工具风险等级（共享定义）
TOOL_RISK_LEVELS = {
    "read_file": "low", "grep": "low", "glob": "low",
    "code_interpreter": "medium", "bash": "medium",
    "write_file": "medium", "edit_file": "medium",
    "spawn_subagent": "medium", "monitor_shell": "medium",
    "set_timer": "low", "send_message_to_agent": "low",
    "ask_user": "low", "notify_user": "low",
}

# 工具调用次数限制
TOOL_CALL_LIMITS = {
    "code_interpreter": 15, "bash": 10,
    "write_file": 30, "edit_file": 20,
}
