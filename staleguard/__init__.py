"""StaleGuard - catch outdated facts in LLM answers before your users do."""
from .models import CheckReport, CheckRequest
from .pipeline import StaleGuard

__all__ = ["StaleGuard", "CheckRequest", "CheckReport"]
__version__ = "0.1.0"
