"""Context compilation layer.

The compiler owns assembly and budget accounting; ``ContextManager`` and
``HierarchicalMemory`` remain compression primitives behind it.
"""

from .models import BudgetPlan, ContextBlock, ContextIR, CompiledContext
from .budget import BudgetManager
from .compiler import ContextCompiler

__all__ = [
    "BudgetManager",
    "BudgetPlan",
    "CompiledContext",
    "ContextBlock",
    "ContextCompiler",
    "ContextIR",
]
