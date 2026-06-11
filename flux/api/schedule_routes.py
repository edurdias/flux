"""Schedule management routes (`/schedules*`).

Part of the ``flux.api`` route modules extracted from ``flux/server.py``. The
routes are defined inside a mixin method so handler closures keep their access
to ``self`` (the ``Server`` instance) and the shared per-app dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


from fastapi import Depends
from fastapi import HTTPException

from flux.catalogs import WorkflowCatalog
from flux.domain.schedule import schedule_factory
from flux.schedule_manager import create_schedule_manager
from flux.security.dependencies import get_identity, require_permission
from flux.security.identity import FluxIdentity
from flux.utils import get_logger
from flux.api.schemas import (
    ScheduleRequest,
    ScheduleResponse,
    ScheduleUpdateRequest,
    ScheduleHistoryEntry,
    ScheduleHistoryResponse,
)

logger = get_logger(__name__)


if TYPE_CHECKING:
    from flux.server import Server


class ScheduleRoutesMixin:
    def _register_schedule_routes(  # type: ignore[misc]
        self: Server,
        api,
        *,
        auth_config,
        auth_service,
        principal_registry,
        limiter,
    ):
        # Scheduling API
        def _schedule_model_to_response(schedule) -> ScheduleResponse:
            """Convert ScheduleModel to ScheduleResponse"""
            return ScheduleResponse(
                id=schedule.id,
                workflow_id=schedule.workflow_id,
                workflow_namespace=schedule.workflow_namespace,
                workflow_name=schedule.workflow_name,
                name=schedule.name,
                description=schedule.description,
                schedule_type=schedule.schedule_type.value,
                status=schedule.status.value,
                created_at=schedule.created_at.isoformat(),
                updated_at=schedule.updated_at.isoformat(),
                last_run_at=schedule.last_run_at.isoformat() if schedule.last_run_at else None,
                next_run_at=schedule.next_run_at.isoformat() if schedule.next_run_at else None,
                run_count=schedule.run_count,
                failure_count=schedule.failure_count,
                run_as_service_account=getattr(schedule, "run_as_service_account", None),
            )

        def _resolve_schedule_id_or_name(schedule_id_or_name: str, schedule_manager):
            """
            Resolve schedule by ID or name.

            First tries to get by ID. If not found, tries to find by name.
            If the input looks like a name (contains underscore or dash), also search by name.

            Args:
                schedule_id_or_name: Either a UUID schedule ID or a schedule name
                schedule_manager: The schedule manager instance

            Returns:
                ScheduleModel if found, None otherwise
            """
            # First try getting by ID (UUID)
            schedule = schedule_manager.get_schedule(schedule_id_or_name)
            if schedule:
                return schedule

            # If not found and looks like it could be a name (not a UUID pattern),
            # search all schedules for a matching name
            if "_" in schedule_id_or_name or "-" in schedule_id_or_name:
                all_schedules = schedule_manager.list_schedules(active_only=False)
                for sched in all_schedules:
                    if sched.name == schedule_id_or_name:
                        return sched

            return None

        @api.post("/schedules", response_model=ScheduleResponse)
        async def create_schedule(
            request: ScheduleRequest,
            identity: FluxIdentity = Depends(require_permission("schedule:*:manage")),
        ):
            """Create a new schedule for a workflow"""
            try:
                logger.info(
                    f"Creating schedule '{request.name}' for workflow '{request.workflow_name}'",
                )

                if auth_config.enabled:
                    if not request.run_as_service_account:
                        raise HTTPException(
                            status_code=400,
                            detail="run_as_service_account is required when auth is enabled",
                        )
                    sa = None
                    if auth_service.principal_registry is not None:
                        sa = auth_service.principal_registry.find(
                            request.run_as_service_account,
                            "flux",
                        )
                    if sa is None:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Service account '{request.run_as_service_account}' not found",
                        )

                # Get workflow from catalog to ensure it exists
                from flux.catalogs import resolve_workflow_ref as _resolve_ref

                if request.workflow_namespace:
                    _sched_req_ns = request.workflow_namespace
                    _sched_req_name = request.workflow_name
                else:
                    _sched_req_ns, _sched_req_name = _resolve_ref(request.workflow_name)
                catalog = WorkflowCatalog.create()
                workflow_def = catalog.get(_sched_req_ns, _sched_req_name)
                if not workflow_def:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Workflow '{request.workflow_name}' not found",
                    )

                # Create schedule from configuration
                schedule = schedule_factory(request.schedule_config)

                # Create schedule via manager
                schedule_manager = create_schedule_manager()
                schedule_model = schedule_manager.create_schedule(
                    workflow_id=workflow_def.id,
                    workflow_namespace=_sched_req_ns,
                    workflow_name=_sched_req_name,
                    name=request.name,
                    schedule=schedule,
                    description=request.description,
                    input_data=request.input_data,
                    run_as_service_account=request.run_as_service_account,
                )

                logger.info(
                    f"Successfully created schedule '{request.name}' with ID '{schedule_model.id}'",
                )
                return _schedule_model_to_response(schedule_model)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error creating schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error creating schedule: {str(e)}")

        @api.get("/schedules", response_model=list[ScheduleResponse])
        async def list_schedules(
            workflow_name: str | None = None,
            active_only: bool = True,
            limit: int | None = None,
            offset: int | None = None,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """List schedules visible to the principal.

            Schedules are filtered per-principal: a schedule is returned only if
            the caller has ``workflow:{namespace}:{workflow_name}:read`` on its
            bound workflow. Optionally filtered by workflow_ref with pagination.
            """
            try:
                logger.debug(
                    f"Listing schedules (workflow: {workflow_name}, active_only: {active_only}, "
                    f"limit: {limit}, offset: {offset})",
                )

                schedule_manager = create_schedule_manager()

                if workflow_name:
                    # Get workflow to get its ID
                    from flux.catalogs import resolve_workflow_ref as _resolve_ref2

                    _list_sched_ns, _list_sched_name = _resolve_ref2(workflow_name)
                    catalog = WorkflowCatalog.create()
                    workflow_def = catalog.get(_list_sched_ns, _list_sched_name)
                    if not workflow_def:
                        raise HTTPException(
                            status_code=404,
                            detail=f"Workflow '{workflow_name}' not found",
                        )

                    schedules = schedule_manager.list_schedules(
                        workflow_id=workflow_def.id,
                        active_only=active_only,
                        limit=limit,
                        offset=offset,
                    )
                else:
                    schedules = schedule_manager.list_schedules(
                        active_only=active_only,
                        limit=limit,
                        offset=offset,
                    )

                # Per-principal filter: keep only schedules whose bound workflow
                # the caller has read access to.
                if auth_service is not None and auth_config.enabled:
                    permissions = await auth_service.resolve_permissions(identity)
                    visible = []
                    for s in schedules:
                        required = f"workflow:{s.workflow_namespace}:{s.workflow_name}:read"
                        if identity.has_permission(required, permissions):
                            visible.append(s)
                    schedules = visible

                result = [_schedule_model_to_response(s) for s in schedules]
                logger.debug(f"Found {len(result)} schedules")
                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error listing schedules: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error listing schedules: {str(e)}")

        @api.get("/schedules/{schedule_id}", response_model=ScheduleResponse)
        async def get_schedule(
            schedule_id: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Get a specific schedule by ID or name.

            Authorized only if the caller has
            ``workflow:{namespace}:{workflow_name}:read`` on the schedule's
            bound workflow.
            """
            try:
                logger.debug(f"Getting schedule '{schedule_id}'")

                schedule_manager = create_schedule_manager()

                # Resolve by ID or name
                schedule = _resolve_schedule_id_or_name(schedule_id, schedule_manager)
                if not schedule:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                if auth_service is not None and auth_config.enabled:
                    required = (
                        f"workflow:{schedule.workflow_namespace}:{schedule.workflow_name}:read"
                    )
                    if not await auth_service.is_authorized(identity, required):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires '{required}'",
                        )

                return _schedule_model_to_response(schedule)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error getting schedule: {str(e)}")

        @api.put("/schedules/{schedule_id}", response_model=ScheduleResponse)
        async def update_schedule(
            schedule_id: str,
            request: ScheduleUpdateRequest,
            identity: FluxIdentity = Depends(require_permission("schedule:*:manage")),
        ):
            """Update an existing schedule (accepts either schedule ID or name)"""
            try:
                logger.info(f"Updating schedule '{schedule_id}'")

                if auth_config.enabled and request.run_as_service_account is not None:
                    sa = None
                    if auth_service.principal_registry is not None:
                        sa = auth_service.principal_registry.find(
                            request.run_as_service_account,
                            "flux",
                        )
                    if sa is None:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Service account '{request.run_as_service_account}' not found",
                        )

                schedule_manager = create_schedule_manager()

                # Resolve by ID or name
                existing_schedule = _resolve_schedule_id_or_name(schedule_id, schedule_manager)
                if not existing_schedule:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                # Build update parameters
                schedule_param = None
                if request.schedule_config is not None:
                    schedule_param = schedule_factory(request.schedule_config)

                # Update using the actual ID
                schedule = schedule_manager.update_schedule(
                    existing_schedule.id,
                    schedule=schedule_param,
                    description=request.description,
                    input_data=request.input_data,
                    run_as_service_account=request.run_as_service_account,
                )

                logger.info(f"Successfully updated schedule '{schedule_id}'")
                return _schedule_model_to_response(schedule)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error updating schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error updating schedule: {str(e)}")

        @api.post("/schedules/{schedule_id}/pause", response_model=ScheduleResponse)
        async def pause_schedule(
            schedule_id: str,
            identity: FluxIdentity = Depends(require_permission("schedule:*:manage")),
        ):
            """Pause a schedule (accepts either schedule ID or name)"""
            try:
                logger.info(f"Pausing schedule '{schedule_id}'")

                schedule_manager = create_schedule_manager()

                # Resolve by ID or name
                schedule = _resolve_schedule_id_or_name(schedule_id, schedule_manager)
                if not schedule:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                # Now pause using the actual ID
                schedule = schedule_manager.pause_schedule(schedule.id)

                logger.info(f"Successfully paused schedule '{schedule_id}'")
                return _schedule_model_to_response(schedule)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error pausing schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error pausing schedule: {str(e)}")

        @api.post("/schedules/{schedule_id}/resume", response_model=ScheduleResponse)
        async def resume_schedule(
            schedule_id: str,
            identity: FluxIdentity = Depends(require_permission("schedule:*:manage")),
        ):
            """Resume a paused schedule (accepts either schedule ID or name)"""
            try:
                logger.info(f"Resuming schedule '{schedule_id}'")

                schedule_manager = create_schedule_manager()

                # Resolve by ID or name
                schedule = _resolve_schedule_id_or_name(schedule_id, schedule_manager)
                if not schedule:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                # Now resume using the actual ID
                schedule = schedule_manager.resume_schedule(schedule.id)

                logger.info(f"Successfully resumed schedule '{schedule_id}'")
                return _schedule_model_to_response(schedule)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error resuming schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error resuming schedule: {str(e)}")

        @api.delete("/schedules/{schedule_id}")
        async def delete_schedule(
            schedule_id: str,
            identity: FluxIdentity = Depends(require_permission("schedule:*:manage")),
        ):
            """Delete a schedule (accepts either schedule ID or name)"""
            try:
                logger.info(f"Deleting schedule '{schedule_id}'")

                schedule_manager = create_schedule_manager()

                # Resolve by ID or name
                schedule = _resolve_schedule_id_or_name(schedule_id, schedule_manager)
                if not schedule:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                # Now delete using the actual ID
                success = schedule_manager.delete_schedule(schedule.id)

                if not success:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                logger.info(f"Successfully deleted schedule '{schedule_id}'")
                return {
                    "status": "success",
                    "message": f"Schedule '{schedule_id}' deleted successfully",
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error deleting schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error deleting schedule: {str(e)}")

        # ===========================================
        # Schedule History Endpoint
        # ===========================================

        @api.get("/schedules/{schedule_id}/history", response_model=ScheduleHistoryResponse)
        async def schedule_history(
            schedule_id: str,
            limit: int = 50,
            offset: int = 0,
            identity: FluxIdentity = Depends(require_permission("schedule:*:read")),
        ):
            """Get execution history for a schedule."""
            try:
                logger.debug(
                    f"Fetching history for schedule '{schedule_id}' "
                    f"(limit: {limit}, offset: {offset})",
                )

                schedule_manager = create_schedule_manager()

                # Resolve by ID or name
                schedule = _resolve_schedule_id_or_name(schedule_id, schedule_manager)
                if not schedule:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                # Get execution history
                entries, total = schedule_manager.get_schedule_history(
                    schedule.id,
                    limit=limit,
                    offset=offset,
                )

                result = ScheduleHistoryResponse(
                    schedule_id=schedule.id,
                    workflow_name=schedule.workflow_name,
                    entries=[
                        ScheduleHistoryEntry(
                            execution_id=e["execution_id"],
                            workflow_name=e["workflow_name"],
                            state=e["state"],
                            started_at=e.get("started_at"),
                            completed_at=e.get("completed_at"),
                            error=e.get("error"),
                        )
                        for e in entries
                    ],
                    total=total,
                    limit=limit,
                    offset=offset,
                )

                logger.debug(f"Found {total} history entries for schedule '{schedule_id}'")
                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting schedule history: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error getting schedule history: {str(e)}",
                )
