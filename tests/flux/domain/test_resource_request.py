from __future__ import annotations


from flux.domain.resource_request import ResourceRequest
from flux.worker_registry import WorkerResouceGPUInfo, WorkerResourcesInfo


def test_matches_worker_basic():
    """Test basic worker resource matching."""
    # Create a resource request with CPU and memory requirements
    request = ResourceRequest(cpu=2, memory="4Gi")

    # Create worker resources that meet the requirements
    worker_resources = WorkerResourcesInfo(
        cpu_total=4,
        cpu_available=3,
        memory_total=8 * 1024 * 1024 * 1024,  # 8Gi
        memory_available=6 * 1024 * 1024 * 1024,  # 6Gi
        disk_total=100 * 1024 * 1024 * 1024,  # 100Gi
        disk_free=80 * 1024 * 1024 * 1024,  # 80Gi
        gpus=[],
    )

    worker_packages = []

    # The worker should match
    assert request.matches_worker(worker_resources, worker_packages)

    # Create worker resources that don't meet the requirements
    insufficient_resources = WorkerResourcesInfo(
        cpu_total=2,
        cpu_available=1,  # Not enough CPU
        memory_total=8 * 1024 * 1024 * 1024,
        memory_available=6 * 1024 * 1024 * 1024,
        disk_total=100 * 1024 * 1024 * 1024,
        disk_free=80 * 1024 * 1024 * 1024,
        gpus=[],
    )

    # The worker should not match
    assert not request.matches_worker(insufficient_resources, worker_packages)


def test_matches_worker_gpu():
    """Test GPU resource matching."""
    # Create a resource request with GPU requirements
    request = ResourceRequest(gpu=1)

    # Create worker resources with GPUs
    worker_resources = WorkerResourcesInfo(
        cpu_total=4,
        cpu_available=3,
        memory_total=8 * 1024 * 1024 * 1024,
        memory_available=6 * 1024 * 1024 * 1024,
        disk_total=100 * 1024 * 1024 * 1024,
        disk_free=80 * 1024 * 1024 * 1024,
        gpus=[
            WorkerResouceGPUInfo(
                name="NVIDIA GeForce RTX 3080",
                memory_total=10 * 1024 * 1024 * 1024,  # 10Gi
                memory_available=8 * 1024 * 1024 * 1024,  # 8Gi
            ),
        ],
    )

    worker_packages = []

    # The worker should match
    assert request.matches_worker(worker_resources, worker_packages)

    # Create worker resources without GPUs
    no_gpu_resources = WorkerResourcesInfo(
        cpu_total=4,
        cpu_available=3,
        memory_total=8 * 1024 * 1024 * 1024,
        memory_available=6 * 1024 * 1024 * 1024,
        disk_total=100 * 1024 * 1024 * 1024,
        disk_free=80 * 1024 * 1024 * 1024,
        gpus=[],
    )

    # The worker should not match
    assert not request.matches_worker(no_gpu_resources, worker_packages)


def test_matches_worker_packages():
    """Test package matching."""
    # Create a resource request with package requirements
    request = ResourceRequest(packages=["numpy>=1.20.0", "pandas", "scikit-learn==1.0.0"])

    # Create worker resources and packages that meet the requirements
    worker_resources = WorkerResourcesInfo(
        cpu_total=4,
        cpu_available=3,
        memory_total=8 * 1024 * 1024 * 1024,
        memory_available=6 * 1024 * 1024 * 1024,
        disk_total=100 * 1024 * 1024 * 1024,
        disk_free=80 * 1024 * 1024 * 1024,
        gpus=[],
    )

    worker_packages = [
        {"name": "numpy", "version": "1.21.0"},
        {"name": "pandas", "version": "1.3.0"},
        {"name": "scikit-learn", "version": "1.0.0"},
    ]

    # The worker should match
    assert request.matches_worker(worker_resources, worker_packages)

    # Create worker packages that don't meet the requirements
    insufficient_packages = [
        {"name": "numpy", "version": "1.19.0"},  # Version too low
        {"name": "pandas", "version": "1.3.0"},
        {"name": "scikit-learn", "version": "1.0.0"},
    ]

    # The worker should not match
    assert not request.matches_worker(worker_resources, insufficient_packages)

    # Test with missing package
    missing_package = [
        {"name": "numpy", "version": "1.21.0"},
        {"name": "scikit-learn", "version": "1.0.0"},
        # pandas is missing
    ]

    # The worker should not match
    assert not request.matches_worker(worker_resources, missing_package)

    # Test with wrong version for exact match
    wrong_version = [
        {"name": "numpy", "version": "1.21.0"},
        {"name": "pandas", "version": "1.3.0"},
        {"name": "scikit-learn", "version": "1.0.1"},  # Version doesn't match exactly
    ]

    # The worker should not match
    assert not request.matches_worker(worker_resources, wrong_version)


def test_memory_parsing():
    """Test memory string parsing."""
    request = ResourceRequest()

    # Test various memory formats
    assert request._parse_memory_to_bytes(1024) == 1024
    assert request._parse_memory_to_bytes("1024") == 1024
    assert request._parse_memory_to_bytes("1Ki") == 1 * 1024
    assert request._parse_memory_to_bytes("1Mi") == 1 * 1024 * 1024
    assert request._parse_memory_to_bytes("1Gi") == 1 * 1024 * 1024 * 1024
    assert request._parse_memory_to_bytes("1Ti") == 1 * 1024 * 1024 * 1024 * 1024
    assert request._parse_memory_to_bytes("1Pi") == 1 * 1024 * 1024 * 1024 * 1024 * 1024

    # Test with decimal values
    assert request._parse_memory_to_bytes("1.5Gi") == int(1.5 * 1024 * 1024 * 1024)


def test_version_comparison():
    """Test version comparison logic."""
    request = ResourceRequest()

    # Test >= operator
    assert request._version_satisfies("1.0.0", "0.9.0", ">=")
    assert request._version_satisfies("1.0.0", "1.0.0", ">=")
    assert not request._version_satisfies("0.9.0", "1.0.0", ">=")

    # Test with different length versions
    assert request._version_satisfies("1.0.0.1", "1.0.0", ">=")
    assert not request._version_satisfies("1.0.0", "1.0.0.1", ">=")

    # Test with non-numeric parts
    assert request._version_satisfies("1.0.0-beta", "1.0.0-alpha", ">=")
    assert not request._version_satisfies("1.0.0-alpha", "1.0.0-beta", ">=")


def test_no_requirements():
    """Test matching when there are no requirements."""
    # Empty resource request should match any worker
    empty_request = ResourceRequest()

    worker_resources = WorkerResourcesInfo(
        cpu_total=1,
        cpu_available=1,
        memory_total=1024,
        memory_available=1024,
        disk_total=1024,
        disk_free=1024,
        gpus=[],
    )

    worker_packages = []

    assert empty_request.matches_worker(worker_resources, worker_packages)
