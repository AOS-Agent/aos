"""n8n error hierarchy.

Maps n8n API errors to structured Python exceptions with
human-readable messages suitable for showing in Qareen.
"""


class N8nError(Exception):
    """Base error for all n8n operations."""

    def __init__(self, message: str, status_code: int | None = None, detail: str | None = None):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class N8nConnectionError(N8nError):
    """Cannot reach n8n on localhost:5678."""

    def __init__(self, detail: str | None = None):
        super().__init__(
            "The automation engine is not responding. It may be starting up.",
            status_code=None,
            detail=detail,
        )


class N8nNotFoundError(N8nError):
    """Workflow or execution not found."""

    def __init__(self, resource: str, resource_id: str):
        super().__init__(
            f"{resource} '{resource_id}' not found.",
            status_code=404,
        )


class N8nValidationError(N8nError):
    """Invalid workflow JSON or parameters."""

    def __init__(self, message: str, detail: str | None = None):
        super().__init__(message, status_code=400, detail=detail)


class N8nCredentialError(N8nError):
    """Credential issue — expired, missing, or invalid."""

    def __init__(self, message: str, detail: str | None = None):
        super().__init__(message, status_code=401, detail=detail)


class N8nExecutionError(N8nError):
    """Workflow execution failed."""

    def __init__(self, workflow_name: str, error_message: str, node_name: str | None = None):
        detail = f"Failed at node '{node_name}'" if node_name else None
        super().__init__(
            f"Automation '{workflow_name}' failed: {error_message}",
            status_code=500,
            detail=detail,
        )
