"""Auto-created schedules: the `<workflow>_auto` row a decorator declares.

Extracted from ``flux.server`` (#264 stage 2). It needs nothing from the
server -- not its state, not its collaborators -- which is why it comes out
first: registration calls it, and a schedule declared on a workflow is a
property of that workflow, not of the process that happened to receive it.

Reconciliation is by workflow *ref*, not by ``workflow_id``: every
registration writes a new workflows row, so a version-scoped lookup would
never find the schedule the previous version created and each redeploy
would add another firing row (#254).
"""

from __future__ import annotations

from typing import Any

from flux.catalogs import WorkflowInfo
from flux.config import Configuration
from flux.schedule_manager import create_schedule_manager
from flux.utils import get_logger
from flux.workflow import workflow

logger = get_logger(__name__)


def auto_create_schedules(source: bytes, workflows: list[WorkflowInfo]) -> None:
    """Auto-create schedules for workflows by executing source and extracting schedule from workflow objects"""
    config = Configuration.get().settings.scheduling

    if not config.auto_schedule_enabled:
        logger.debug("Auto-scheduling disabled in configuration")
        return

    try:
        module_globals: dict[str, Any] = {}
        exec(source, module_globals)

        schedule_manager = create_schedule_manager()

        for workflow_info in workflows:
            workflow_obj = None

            for obj in module_globals.values():
                if (
                    isinstance(obj, workflow)
                    and obj.namespace == workflow_info.namespace
                    and obj.name == workflow_info.name
                ):
                    workflow_obj = obj
                    break

            if workflow_obj is None or workflow_obj.schedule is None:
                continue

            schedule_name = f"{workflow_info.name}{config.auto_schedule_suffix}"

            try:
                # Looked up by ref, not by workflow_id: every
                # registration writes a new workflows row, so a
                # version-scoped lookup never finds the schedule the
                # previous version created and each redeploy added a
                # second '<name>_auto' row -- firing the workflow once
                # per surviving version on every tick.
                existing_schedules = schedule_manager.list_schedules_for_workflow_ref(
                    workflow_info.namespace,
                    workflow_info.name,
                )
                matches = [s for s in existing_schedules if s.name == schedule_name]
                # A database written by the version-scoped lookup holds
                # one '<name>_auto' row per registered version, all of
                # them firing. Reconciling means converging on one:
                # keep the oldest (list is ordered by creation) and drop
                # the rest, before the survivor is re-pointed -- the
                # (workflow_id, name) unique constraint would otherwise
                # collide with a duplicate already on the new version.
                existing = matches[0] if matches else None
                for duplicate in matches[1:]:
                    schedule_manager.delete_schedule(duplicate.id)
                    logger.info(
                        f"Removed duplicate auto-schedule '{schedule_name}' "
                        f"for workflow '{workflow_info.name}'",
                    )

                if existing:
                    schedule_manager.update_schedule(
                        schedule_id=existing.id,
                        schedule=workflow_obj.schedule,
                        description="Auto-created from workflow decorator",
                        workflow_id=workflow_info.id,
                    )
                    logger.info(
                        f"Updated auto-schedule '{schedule_name}' for workflow '{workflow_info.name}'",
                    )
                else:
                    schedule_manager.create_schedule(
                        workflow_id=workflow_info.id,
                        workflow_namespace=workflow_info.namespace,
                        workflow_name=workflow_info.name,
                        name=schedule_name,
                        schedule=workflow_obj.schedule,
                        description="Auto-created from workflow decorator",
                        input_data=None,
                    )
                    logger.info(
                        f"Created auto-schedule '{schedule_name}' for workflow '{workflow_info.name}'",
                    )

            except Exception as e:
                logger.error(
                    f"Failed to auto-create schedule for workflow '{workflow_info.name}': {str(e)}",
                    exc_info=True,
                )

    except Exception as e:
        logger.error(
            f"Failed to execute workflow source for schedule extraction: {str(e)}",
            exc_info=True,
        )


# ===========================================
# Internal Execution Helper
# ===========================================
