#!/usr/bin/env python
# Debug the pause functionality

import asyncio
import json
from flux.context_managers import ContextManager
from examples.multiple_pause_points import multi_pause_workflow

def show_events(ctx, title="Current events"):
    """Pretty print events in the execution context"""
    print("\n" + "="*80)
    print(f"{title} (Context ID: {ctx.execution_id})")
    print("="*80)
    print(f"State: {ctx.state.value}")
    print(f"Is paused: {ctx.is_paused}")
    print(f"Has resumed: {ctx.has_resumed}")
    print("\nEvents:")
    for i, event in enumerate(ctx.events):
        print(f"{i+1}. Type: {event.type.value}, Name: {event.name}, Value: {event.value}")
    print("="*80 + "\n")

# Run initial workflow to first pause point
print("\nStarting workflow...")
ctx1 = multi_pause_workflow.run()
show_events(ctx1, "After first run")

# Resume from first pause point
print("\nResuming from first pause...")
ctx2 = multi_pause_workflow.run(execution_id=ctx1.execution_id)
show_events(ctx2, "After first resume")

# Try to look up the context directly from storage
try:
    print("\nLooking up context from storage...")
    manager = ContextManager.create()
    stored_ctx = manager.get(ctx2.execution_id)
    show_events(stored_ctx, "Context from storage")
except Exception as e:
    print(f"Error retrieving from storage: {e}")

print("\nDone!")
