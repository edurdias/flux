from flux.server import WorkerRegistration, WorkerResponse


def test_worker_registration_with_labels():
    reg = WorkerRegistration(
        name="w1",
        runtime={"os_name": "Linux", "os_version": "6.0", "python_version": "3.12"},
        packages=[],
        resources={
            "cpu_total": 4,
            "cpu_available": 4,
            "memory_total": 8e9,
            "memory_available": 8e9,
            "disk_total": 100e9,
            "disk_free": 100e9,
            "gpus": [],
        },
        labels={"role": "harness", "browser": "true"},
    )
    assert reg.labels == {"role": "harness", "browser": "true"}


def test_worker_registration_default_labels():
    reg = WorkerRegistration(
        name="w1",
        runtime={"os_name": "Linux", "os_version": "6.0", "python_version": "3.12"},
        packages=[],
        resources={
            "cpu_total": 4,
            "cpu_available": 4,
            "memory_total": 8e9,
            "memory_available": 8e9,
            "disk_total": 100e9,
            "disk_free": 100e9,
            "gpus": [],
        },
    )
    assert reg.labels == {}


def test_worker_response_with_labels():
    resp = WorkerResponse(name="w1", status="online", labels={"role": "harness"})
    assert resp.labels == {"role": "harness"}


def test_worker_response_default_labels():
    resp = WorkerResponse(name="w1")
    assert resp.labels == {}
