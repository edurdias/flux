from flux import task, workflow


def fallback_for_bad_task(number):
    print(f"Fallback for task #{number}")


@task(fallback=fallback_for_bad_task, retry_max_attemps=2)
def bad_task(number: int, should_fail: bool = True):
    if should_fail:
        print(f"Failed task #{number}")
        raise ValueError()
    print(f"Succeed task #{number}")


@workflow
def fallback_after_retry():
    yield bad_task(1)
    yield bad_task(2, False)  # will pass


if __name__ == "__main__":
    ctx = fallback_after_retry.run()
    print(ctx.to_json())
