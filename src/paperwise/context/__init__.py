"""Context compilation layer.

The compiler owns assembly and budget accounting; ``ContextManager`` and
``HierarchicalMemory`` remain compression primitives behind it.
"""

from .models import BudgetPlan, ContextBlock, ContextIR, CompiledContext
from .budget import BudgetManager
from .compiler import ContextCompiler
from .activation import select_items

__all__ = [
    "BudgetManager",
    "BudgetPlan",
    "CompiledContext",
    "ContextBlock",
    "ContextCompiler",
    "ContextIR",
    "select_items",
]
