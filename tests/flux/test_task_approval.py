import inspect

from flux import task


def test_task_init_accepts_requires_approval_kwarg():
    sig = inspect.signature(task.__init__)
    assert "requires_approval" in sig.parameters
    assert sig.parameters["requires_approval"].default is False


def test_with_options_decorator_sets_requires_approval_true():
    @task.with_options(requires_approval=True)
    async def t():
        return 1

    assert t.requires_approval is True


def test_with_options_decorator_sets_requires_approval_callable():
    pred = lambda amount: amount > 100  # noqa: E731

    @task.with_options(requires_approval=pred)
    async def t(amount: int):
        return amount

    assert t.requires_approval is pred


def test_with_options_method_preserves_existing_value_when_none():
    @task
    async def t():
        return 1

    base = t.with_options(requires_approval=True)
    derived = base.with_options()
    assert derived.requires_approval is True


def test_with_options_method_can_explicitly_set_false():
    @task
    async def t():
        return 1

    base = t.with_options(requires_approval=True)
    derived = base.with_options(requires_approval=False)
    assert derived.requires_approval is False


def test_default_requires_approval_is_false():
    @task
    async def t():
        return 1

    assert t.requires_approval is False
