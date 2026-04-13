import textwrap


from flux.catalogs import extract_workflow_description, extract_workflow_input_schema


class TestExtractWorkflowInputSchema:
    def test_pydantic_model_input(self):
        source = textwrap.dedent(
            """
            from pydantic import BaseModel
            from flux import workflow, ExecutionContext

            class InvoiceInput(BaseModel):
                customer_id: str
                amount: float
                due_date: str | None = None

            @workflow
            async def invoice(ctx: ExecutionContext[InvoiceInput]):
                pass
        """,
        )
        schema = extract_workflow_input_schema(source.encode(), "invoice")
        assert schema is not None
        assert "properties" in schema
        assert "customer_id" in schema["properties"]
        assert "amount" in schema["properties"]

    def test_dict_input_returns_none(self):
        source = textwrap.dedent(
            """
            from typing import Any
            from flux import workflow, ExecutionContext

            @workflow
            async def refund(ctx: ExecutionContext[dict[str, Any]]):
                pass
        """,
        )
        schema = extract_workflow_input_schema(source.encode(), "refund")
        assert schema is None

    def test_str_input_returns_none(self):
        source = textwrap.dedent(
            """
            from flux import workflow, ExecutionContext

            @workflow
            async def hello(ctx: ExecutionContext[str]):
                pass
        """,
        )
        schema = extract_workflow_input_schema(source.encode(), "hello")
        assert schema is None

    def test_no_type_param_returns_none(self):
        source = textwrap.dedent(
            """
            from flux import workflow, ExecutionContext

            @workflow
            async def bare(ctx: ExecutionContext):
                pass
        """,
        )
        schema = extract_workflow_input_schema(source.encode(), "bare")
        assert schema is None

    def test_missing_workflow_returns_none(self):
        source = textwrap.dedent(
            """
            from flux import workflow, ExecutionContext

            @workflow
            async def other(ctx: ExecutionContext[str]):
                pass
        """,
        )
        schema = extract_workflow_input_schema(source.encode(), "nonexistent")
        assert schema is None


class TestExtractWorkflowDescription:
    def test_docstring_extracted(self):
        source = textwrap.dedent(
            '''
            from flux import workflow, ExecutionContext

            @workflow
            async def invoice(ctx: ExecutionContext[str]):
                """Process an invoice for a customer."""
                pass
        ''',
        )
        desc = extract_workflow_description(source.encode(), "invoice")
        assert desc == "Process an invoice for a customer."

    def test_no_docstring_returns_none(self):
        source = textwrap.dedent(
            """
            from flux import workflow, ExecutionContext

            @workflow
            async def invoice(ctx: ExecutionContext[str]):
                pass
        """,
        )
        desc = extract_workflow_description(source.encode(), "invoice")
        assert desc is None


class TestBatchEnrichment:
    def test_multi_workflow_file_extracts_all(self):
        from flux.catalogs import _enrich_workflow_metadata, WorkflowInfo

        source = textwrap.dedent(
            """
            from pydantic import BaseModel
            from flux import workflow, ExecutionContext

            class InputA(BaseModel):
                x: int

            @workflow
            async def wf_a(ctx: ExecutionContext[InputA]):
                \"\"\"Workflow A.\"\"\"
                pass

            @workflow
            async def wf_b(ctx: ExecutionContext[str]):
                \"\"\"Workflow B.\"\"\"
                pass
        """,
        )
        wf_a = WorkflowInfo(id="a", name="wf_a", imports=[], source=source.encode(), metadata={})
        wf_b = WorkflowInfo(id="b", name="wf_b", imports=[], source=source.encode(), metadata={})
        _enrich_workflow_metadata(source.encode(), [wf_a, wf_b])

        assert wf_a.metadata.get("input_schema") is not None
        assert "x" in wf_a.metadata["input_schema"]["properties"]
        assert wf_a.metadata.get("description") == "Workflow A."

        assert wf_b.metadata.get("input_schema") is None
        assert wf_b.metadata.get("description") == "Workflow B."
