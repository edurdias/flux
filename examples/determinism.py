from flux import workflow
from flux.tasks import now, randint, randrange, uuid4


@workflow
def determinism():
    start = yield now()
    yield uuid4()
    yield randint(1, 5)
    yield randrange(1, 10)
    end = yield now()
    return end - start


if __name__ == "__main__":
    ctx = determinism.run()
    print(ctx.to_json())