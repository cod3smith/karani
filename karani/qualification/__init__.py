from . import usage
from .agent import qualify_one_agent
from .client import QualifierClient, qualify_one
from .factory import get_qualifier
from .models import QualificationResult, QualStrength, QualGap
from .runner import qualify_pending
from .tools import DEFAULT_TOOLS, Tool, ToolRegistry, default_registry

# Direct provider access, if you'd rather instantiate manually:
from .openrouter import OpenRouterQualifier

__all__ = [
    "usage",
    "QualifierClient", "qualify_one", "qualify_one_agent",
    "get_qualifier",
    "QualificationResult", "QualStrength", "QualGap",
    "qualify_pending", "OpenRouterQualifier",
    "Tool", "ToolRegistry", "DEFAULT_TOOLS", "default_registry",
]

# AnthropicQualifier is optional — only importable if the SDK is installed.
try:
    from .anthropic import AnthropicQualifier  # noqa: F401
    __all__.append("AnthropicQualifier")
except ImportError:
    pass
