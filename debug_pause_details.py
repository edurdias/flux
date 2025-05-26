#!/usr/bin/env python
# Debug the pause functionality

from examples.multiple_pause_points import multi_pause_workflow
from flux.domain.events import ExecutionEventType

# Run initial workflow to first pause point
print("\nStarting workflow...")
ctx = multi_pause_workflow.run()
print(f"First run - is_paused: {ctx.is_paused}, has_resumed: {ctx.has_resumed}")

# Show pause events
pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
print(f"Pause events after first run: {[e.value for e in pause_events]}")

# Resume from first pause point
print("\nResuming from first pause...")
ctx = multi_pause_workflow.run(execution_id=ctx.execution_id)
print(f"Second run - is_paused: {ctx.is_paused}, has_resumed: {ctx.has_resumed}")

# Show pause events after second run
pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
print(f"Pause events after second run: {[e.value for e in pause_events]}")

# Print all events to see what's happening
print("\nAll events after second run:")
for i, event in enumerate(ctx.events):
    print(f"{i+1}. {event.type.value}: {event.value}")

print("\nDone!")
