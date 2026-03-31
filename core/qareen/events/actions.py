"""Qareen Governed Actions — Decorator-based action system.

Every mutation in AOS goes through a governed action. The action system
provides: validation, execution, audit logging, and event emission.

Usage:
    registry = ActionRegistry(bus=event_bus, audit_log=audit_log)

    @action("create_task", emits="task.created")
    async def create_task(title: str, project: str | None = None) -> dict:
        ...
        return {"task_id": "aos#42", "title": title}

    registry.register(create_task)
    result = await registry.execute("create_task", {"title": "Fix bug"}, actor="chief")
"""

from __future__ import annotations

import functools
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from .audit import AuditEntry, AuditLog
from .bus import EventBus
from .types import Event

logger = logging.getLogger(__name__)

# Type alias for async action functions
ActionFunc = Callable[..., Coroutine[Any, Any, Any]]

# Type alias for validators: (params) -> list of error strings (empty = valid)
Validator = Callable[[dict[str, Any]], list[str]]

# Type alias for side-effect hooks: (action_name, params, result) -> None
SideEffect = Callable[[str, dict[str, Any], Any], Coroutine[Any, Any, None]]


# ---------------------------------------------------------------------------
# Action definition
# ---------------------------------------------------------------------------

@dataclass
class ActionDefinition:
    """Metadata and behavior attached to a governed action.

    Attributes:
        name: Unique dot-notation name (e.g. "create_task", "send_message").
        emits: Event type string emitted on successful execution.
        param_schema: JSON-schema-style dict describing expected parameters.
                      Used for validation before execution.
        validator: Optional callable that receives params dict and returns
                   a list of error strings. Empty list = valid.
        side_effects: Callables invoked after successful execution.
                      Receive (action_name, params, result).
        func: The async function that performs the action.
        description: Human-readable description of what this action does.
        requires_approval: Whether this action needs operator approval
                           before execution (trust level gating).
    """

    name: str
    emits: str | None = None
    param_schema: dict[str, Any] = field(default_factory=dict)
    validator: Validator | None = None
    side_effects: list[SideEffect] = field(default_factory=list)
    func: ActionFunc | None = None
    description: str = ""
    requires_approval: bool = False


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def action(
    name: str,
    *,
    emits: str | None = None,
    param_schema: dict[str, Any] | None = None,
    validator: Validator | None = None,
    side_effects: list[SideEffect] | None = None,
    description: str = "",
    requires_approval: bool = False,
) -> Callable[[ActionFunc], ActionFunc]:
    """Decorator that marks an async function as a governed action.

    The decorated function gains an `_action_def` attribute containing
    its ActionDefinition, which can be read by ActionRegistry.register().

    Args:
        name: Unique action name (e.g. "create_task").
        emits: Event type string to emit on success (e.g. "task.created").
        param_schema: JSON-schema-style dict for parameter validation.
        validator: Callable (params) -> list[str] of error messages.
        side_effects: Post-execution async hooks.
        description: Human-readable description.
        requires_approval: Whether operator approval is needed.

    Returns:
        Decorator that attaches ActionDefinition to the function.

    Example:
        @action("create_task", emits="task.created")
        async def create_task(title: str, project: str = None) -> dict:
            ...
    """
    def decorator(func: ActionFunc) -> ActionFunc:
        definition = ActionDefinition(
            name=name,
            emits=emits,
            param_schema=param_schema or {},
            validator=validator,
            side_effects=side_effects or [],
            func=func,
            description=description or func.__doc__ or "",
            requires_approval=requires_approval,
        )

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await func(*args, **kwargs)

        wrapper._action_def = definition  # type: ignore[attr-defined]
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------

class ActionRegistry:
    """Registry of governed actions with full execution lifecycle.

    The registry is the single entry point for executing mutations.
    It enforces: validation -> execution -> audit -> event emission -> side effects.

    Attributes:
        bus: EventBus for publishing events on action completion.
        audit_log: AuditLog for recording every action attempt.
    """

    def __init__(self, bus: EventBus, audit_log: AuditLog) -> None:
        self._bus = bus
        self._audit_log = audit_log
        self._actions: dict[str, ActionDefinition] = {}

    def register(self, func_or_definition: ActionFunc | ActionDefinition) -> None:
        """Register a governed action.

        Accepts either:
          - An async function decorated with @action (has _action_def attribute)
          - An ActionDefinition directly

        Args:
            func_or_definition: The action to register.

        Raises:
            ValueError: If the function lacks an _action_def attribute and
                        is not an ActionDefinition.
            KeyError: If an action with the same name is already registered.
        """
        if isinstance(func_or_definition, ActionDefinition):
            definition = func_or_definition
        elif hasattr(func_or_definition, "_action_def"):
            definition = func_or_definition._action_def
        else:
            raise ValueError(
                f"Cannot register {func_or_definition!r}: "
                "must be decorated with @action or be an ActionDefinition"
            )

        if definition.name in self._actions:
            raise KeyError(
                f"Action '{definition.name}' is already registered"
            )

        self._actions[definition.name] = definition
        logger.info("Registered action: %s", definition.name)

    def unregister(self, name: str) -> None:
        """Remove an action from the registry.

        Args:
            name: The action name to remove.

        Raises:
            KeyError: If no action with this name is registered.
        """
        if name not in self._actions:
            raise KeyError(f"Action '{name}' is not registered")
        del self._actions[name]
        logger.info("Unregistered action: %s", name)

    def get(self, name: str) -> ActionDefinition | None:
        """Look up an action definition by name.

        Args:
            name: The action name.

        Returns:
            The ActionDefinition, or None if not found.
        """
        return self._actions.get(name)

    def list_actions(self) -> list[str]:
        """Return all registered action names.

        Returns:
            Sorted list of action name strings.
        """
        return sorted(self._actions.keys())

    async def execute(
        self,
        name: str,
        params: dict[str, Any],
        *,
        actor: str = "system",
    ) -> dict[str, Any]:
        """Execute a governed action with full lifecycle.

        Lifecycle:
          1. Look up action definition (error if not found)
          2. Validate params via param_schema and validator
          3. Execute the action function
          4. Log to audit trail (success or failure)
          5. Emit event via EventBus (if emits is set)
          6. Run side effects
          7. Return the action's result

        Args:
            name: The registered action name.
            params: Dict of parameters to pass to the action function.
            actor: Identity of who is executing (agent id or "operator").

        Returns:
            Dict with keys: success, result (on success), error (on failure),
            audit_id.
        """
        definition = self.get(name)
        if not definition:
            return {"success": False, "error": f"Unknown action: {name}"}

        # 1. Validate
        errors = await self._validate(definition, params)
        if errors:
            audit_id = await self._record_audit(
                definition, params, actor, result=None,
                success=False, error="; ".join(errors),
            )
            return {"success": False, "error": "; ".join(errors), "audit_id": audit_id}

        # 2. Execute
        start = time.time()
        try:
            if definition.func is None:
                raise RuntimeError(f"Action '{name}' has no implementation")
            result = await definition.func(**params)
        except Exception as e:
            duration_ms = (time.time() - start) * 1000
            audit_id = await self._record_audit(
                definition, params, actor, result=None,
                success=False, error=str(e), duration_ms=duration_ms,
            )
            logger.warning("Action '%s' failed: %s", name, e)
            return {"success": False, "error": str(e), "audit_id": audit_id}

        duration_ms = (time.time() - start) * 1000

        # 3. Audit
        audit_id = await self._record_audit(
            definition, params, actor, result=result,
            success=True, duration_ms=duration_ms,
        )

        # 4. Emit event
        await self._emit_event(definition, params, result)

        # 5. Side effects
        await self._run_side_effects(definition, params, result)

        return {"success": True, "result": result, "audit_id": audit_id}

    async def _validate(
        self, definition: ActionDefinition, params: dict[str, Any]
    ) -> list[str]:
        """Run validation on params for an action.

        Args:
            definition: The action definition containing schema and validator.
            params: The parameters to validate.

        Returns:
            List of error message strings. Empty list means valid.
        """
        errors: list[str] = []

        # Check required fields from param_schema
        if definition.param_schema:
            required = definition.param_schema.get("required", [])
            for field_name in required:
                if field_name not in params or params[field_name] is None:
                    errors.append(f"Missing required parameter: {field_name}")

        # Run custom validator
        if definition.validator:
            try:
                validator_errors = definition.validator(params)
                if validator_errors:
                    errors.extend(validator_errors)
            except Exception as e:
                errors.append(f"Validation error: {e}")

        return errors

    async def _emit_event(
        self, definition: ActionDefinition, params: dict[str, Any], result: Any
    ) -> None:
        """Emit the event associated with a successful action.

        Args:
            definition: The action definition (reads emits field).
            params: The action parameters (included in event payload).
            result: The action result (included in event payload).
        """
        if not definition.emits or not self._bus:
            return

        try:
            event = Event(
                event_type=definition.emits,
                source="action_registry",
                payload={
                    "action": definition.name,
                    "result": result,
                },
            )
            await self._bus.emit(event)
        except Exception:
            logger.exception("Failed to emit event for action '%s'", definition.name)

    async def _run_side_effects(
        self, definition: ActionDefinition, params: dict[str, Any], result: Any
    ) -> None:
        """Run all side-effect hooks for an action.

        Side effects are fire-and-forget: failures are logged but do not
        propagate.

        Args:
            definition: The action definition containing side_effects list.
            params: The action parameters.
            result: The action result.
        """
        for effect in definition.side_effects:
            try:
                await effect(definition.name, params, result)
            except Exception:
                logger.exception(
                    "Side effect failed for action '%s'", definition.name
                )

    async def _record_audit(
        self,
        definition: ActionDefinition,
        params: dict[str, Any],
        actor: str,
        result: Any,
        *,
        success: bool,
        error: str | None = None,
        duration_ms: float = 0.0,
    ) -> str:
        """Write an audit log entry for an action execution.

        Args:
            definition: The action definition.
            params: The action parameters.
            actor: Who executed the action.
            result: The action result (None on failure).
            success: Whether the action succeeded.
            error: Error message if failed.
            duration_ms: Execution duration in milliseconds.

        Returns:
            The audit entry ID.
        """
        entry = AuditEntry(
            actor=actor,
            action_name=definition.name,
            params=params,
            result="success" if success else "failure",
            error=error,
            duration_ms=duration_ms,
            event_emitted=definition.emits if success else None,
        )

        try:
            await self._audit_log.log(entry)
        except Exception:
            logger.exception("Failed to write audit entry for '%s'", definition.name)

        return entry.id
