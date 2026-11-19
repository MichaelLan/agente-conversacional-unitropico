from collections.abc import Callable, Mapping
from typing import Protocol

from unitropico_api.domain.admin import (
    ACTIVE_WORKFLOW_STATUSES,
    Conflict,
    IngestionLaunchError,
    IngestionOutcome,
    IngestionRequest,
    WorkflowState,
    instance_id,
)

ACTIVE = ACTIVE_WORKFLOW_STATUSES
RESTARTABLE = frozenset({"errored", "terminated"})


class WorkflowInstance(Protocol):
    """Subset of a Workflow instance handle."""

    async def status(self) -> object:
        """Return {"status", "output", "error"}."""
        ...

    async def restart(self) -> object:
        """Restart the instance with its original params."""
        ...


class WorkflowBinding(Protocol):
    """Subset of the Workflow binding."""

    async def create(self, *, id: str, params: dict[str, str]) -> WorkflowInstance:
        """Create an instance; fails with instance.already_exists if taken."""
        ...

    async def get(self, id: str) -> WorkflowInstance:
        """Return an existing instance; fails if it does not exist."""
        ...


def _field(data: object, name: str) -> object:
    """Read a key from a converted mapping or a JS proxy."""
    if isinstance(data, Mapping):
        return data.get(name)
    return getattr(data, name, None)


def _state(instance: str, raw: object) -> WorkflowState:
    """Normalize a status object into a WorkflowState."""
    error = _field(raw, "error")
    name = _field(error, "name") if error is not None else None
    message = _field(error, "message") if error is not None else None
    return WorkflowState(
        instance_id=instance,
        status=str(_field(raw, "status") or "unknown"),
        error_name=str(name)[:200] if name else None,
        error_message=str(message)[:500] if message else None,
    )


class WorkflowIngestionLauncher:
    """Ingestion launcher over a Workflow binding resolved at call time."""

    def __init__(self, binding: Callable[[], WorkflowBinding | None]) -> None:
        """Receive a provider of the current request's Workflow binding."""
        self._binding = binding

    def _workflow(self) -> WorkflowBinding:
        """Return the binding or raise a launch error."""
        binding = self._binding()
        if binding is None:
            raise IngestionLaunchError("BindingMissing")
        return binding

    async def launch(self, request: IngestionRequest) -> IngestionOutcome:
        """Create the instance, reuse it if it exists, restart it if it errored."""
        return await self._start(request, restart_settled=False)

    async def relaunch(self, request: IngestionRequest) -> IngestionOutcome:
        """Create or restart the instance; Conflict if it is still active."""
        return await self._start(request, restart_settled=True)

    async def status(self, version_id: str) -> WorkflowState | None:
        """Return the instance status, or None if it does not exist."""
        name = instance_id(version_id)
        workflow = self._workflow()
        try:
            instance = await workflow.get(name)
        except Exception as error:  # JS exceptions have no common base.
            if "not_found" in str(error):
                return None
            raise IngestionLaunchError(type(error).__name__) from None
        try:
            return _state(name, await instance.status())
        except Exception as error:  # JS exceptions have no common base.
            raise IngestionLaunchError(type(error).__name__) from None

    async def _start(
        self, request: IngestionRequest, *, restart_settled: bool
    ) -> IngestionOutcome:
        """Create, or reuse/restart an existing instance with the same id."""
        name = instance_id(request.version_id)
        workflow = self._workflow()
        try:
            await workflow.create(id=name, params=request.as_params())
            return IngestionOutcome(
                instance_id=name, outcome="created", status="queued"
            )
        except Exception as error:  # JS exceptions have no common base.
            if "already_exists" not in str(error):
                raise IngestionLaunchError(type(error).__name__) from None
        try:
            instance = await workflow.get(name)
            state = _state(name, await instance.status())
            if state.status in ACTIVE:
                if restart_settled:
                    raise Conflict(
                        "ingestion_in_progress",
                        "La versión ya se está procesando.",
                    )
                return IngestionOutcome(
                    instance_id=name, outcome="existing", status=state.status
                )
            if state.status in RESTARTABLE or restart_settled:
                await instance.restart()
                return IngestionOutcome(
                    instance_id=name, outcome="restarted", status="queued"
                )
            return IngestionOutcome(
                instance_id=name, outcome="existing", status=state.status
            )
        except Conflict:
            raise
        except Exception as error:  # JS exceptions have no common base.
            raise IngestionLaunchError(type(error).__name__) from None
