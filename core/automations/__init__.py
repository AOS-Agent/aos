"""AOS Automations — n8n integration layer.

Provides a Python client for the n8n REST API and a recipe-based
workflow generation system. n8n runs headlessly on localhost:5678;
this package is the bridge between Qareen and the automation engine.
"""

from .client import N8nClient
from .errors import N8nConnectionError, N8nError

__all__ = ["N8nClient", "N8nError", "N8nConnectionError"]
