from __future__ import annotations

from flux import task, workflow


@task
async def compute_total(line_items: list[dict]) -> float:
    return sum(item["amount"] for item in line_items)


@workflow.with_options(namespace="billing")
async def invoice(ctx):
    line_items = ctx.input or []
    total = await compute_total(line_items)
    return {"invoice_id": "inv_001", "total": total}


if __name__ == "__main__":  # pragma: no cover
    result = invoice.run([{"amount": 10.0}, {"amount": 5.5}])
    print(result.output)
