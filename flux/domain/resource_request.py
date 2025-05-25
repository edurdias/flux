from __future__ import annotations

from flux.worker_registry import WorkerResourcesInfo


class ResourceRequest:
    def __init__(
        self,
        memory: str | int | None = None,
        cpu: int | None = None,
        disk: int | None = None,
        gpu: int | None = None,
        packages: list[str] | None = None,
    ):
        self.memory = memory
        self.cpu = cpu
        self.disk = disk
        self.gpu = gpu
        self.packages = packages

    @classmethod
    def with_memory(cls, memory: str | int) -> ResourceRequest:
        """
        Create a WorkflowRequests object with specified memory.

        Args:
            memory (str | int): The memory requirement.

        Returns:
            WorkflowRequests: A new instance with the specified memory.
        """
        return cls(memory=memory)

    @classmethod
    def with_cpu(cls, cpu: int) -> ResourceRequest:
        """
        Create a WorkflowRequests object with specified CPU.

        Args:
            cpu (int): The CPU requirement.

        Returns:
            WorkflowRequests: A new instance with the specified CPU.
        """
        return cls(cpu=cpu)

    @classmethod
    def with_disk(cls, disk: int) -> ResourceRequest:
        """
        Create a WorkflowRequests object with specified disk.

        Args:
            disk (int): The disk requirement.

        Returns:
            WorkflowRequests: A new instance with the specified disk.
        """
        return cls(disk=disk)

    @classmethod
    def with_gpu(cls, gpu: int) -> ResourceRequest:
        """
        Create a WorkflowRequests object with specified GPU.

        Args:
            gpu (int): The GPU requirement.

        Returns:
            WorkflowRequests: A new instance with the specified GPU.
        """
        return cls(gpu=gpu)

    @classmethod
    def with_packages(cls, packages: list[str]) -> ResourceRequest:
        """
        Create a WorkflowRequests object with specified packages.

        Args:
            packages (list[str]): The list of required packages.

        Returns:
            WorkflowRequests: A new instance with the specified packages.
        """
        return cls(packages=packages)

    def matches_worker(
        self,
        worker_resources: WorkerResourcesInfo,
        worker_packages: list[dict[str, str]],
    ) -> bool:
        """
        Check if a worker's resources and packages match the requirements.

        Args:
            worker_resources: The worker's resource information (WorkerResourcesInfo)
            worker_packages: The worker's installed packages (list of dict with name/version)

        Returns:
            bool: True if the worker meets all requirements, False otherwise
        """
        # If no requirements, always match
        if all(
            attr is None for attr in [self.cpu, self.memory, self.disk, self.gpu, self.packages]
        ):
            return True

        # Check resource requirements
        if not self._check_cpu_requirement(worker_resources):
            return False

        if not self._check_memory_requirement(worker_resources):
            return False

        if not self._check_disk_requirement(worker_resources):
            return False

        if not self._check_gpu_requirement(worker_resources):
            return False

        # Check package requirements
        if not self._check_package_requirements(worker_packages):
            return False

        # All requirements met
        return True

    def _check_cpu_requirement(self, worker_resources: WorkerResourcesInfo) -> bool:
        """Check if worker meets CPU requirements."""
        if self.cpu is None:
            return True

        return (
            hasattr(worker_resources, "cpu_available")
            and worker_resources.cpu_available >= self.cpu
        )

    def _check_memory_requirement(self, worker_resources: WorkerResourcesInfo) -> bool:
        """Check if worker meets memory requirements."""
        if self.memory is None:
            return True

        # Convert string memory values (e.g., "4Gi") to bytes for comparison
        required_memory = self._parse_memory_to_bytes(self.memory)
        return (
            hasattr(worker_resources, "memory_available")
            and worker_resources.memory_available >= required_memory
        )

    def _check_disk_requirement(self, worker_resources: WorkerResourcesInfo) -> bool:
        """Check if worker meets disk space requirements."""
        if self.disk is None:
            return True

        return hasattr(worker_resources, "disk_free") and worker_resources.disk_free >= self.disk

    def _check_gpu_requirement(self, worker_resources: WorkerResourcesInfo) -> bool:
        """Check if worker meets GPU requirements."""
        if self.gpu is None:
            return True

        # If no GPUs available but required
        if not hasattr(worker_resources, "gpus") or not worker_resources.gpus:
            return False

        # Count available GPUs (with available memory)
        available_gpus = sum(1 for gpu in worker_resources.gpus if gpu.memory_available > 0)
        return available_gpus >= self.gpu

    def _check_package_requirements(self, worker_packages: list[dict[str, str]]) -> bool:
        """Check if worker meets package requirements."""
        if not self.packages:
            return True

        worker_package_dict = {pkg["name"].lower(): pkg["version"] for pkg in worker_packages}

        for required_pkg in self.packages:
            if not self._check_single_package(required_pkg, worker_package_dict):
                return False

        return True

    def _check_single_package(self, required_pkg: str, worker_package_dict: dict[str, str]) -> bool:
        """Check if a single package requirement is satisfied."""
        # Parse package name and version constraint
        if ">=" in required_pkg:
            pkg_name, min_version = required_pkg.split(">=")
            pkg_name = pkg_name.strip().lower()
            min_version = min_version.strip()

            # Check if package exists
            if pkg_name not in worker_package_dict:
                return False

            # Check version constraint
            worker_version = worker_package_dict[pkg_name]
            return self._version_satisfies(worker_version, min_version, ">=")
        elif "==" in required_pkg:
            pkg_name, exact_version = required_pkg.split("==")
            pkg_name = pkg_name.strip().lower()
            exact_version = exact_version.strip()

            # Check if package exists with exact version
            if pkg_name not in worker_package_dict:
                return False

            # Exact version match
            worker_version = worker_package_dict[pkg_name]
            return worker_version == exact_version
        else:
            # Just check if package exists
            pkg_name = required_pkg.strip().lower()
            return pkg_name in worker_package_dict

    def _parse_memory_to_bytes(self, memory_str) -> int:
        """
        Convert a memory string (like "4Gi" or "512Mi") to bytes.

        Args:
            memory_str: Memory string with optional unit

        Returns:
            int: Memory in bytes
        """
        if isinstance(memory_str, int):
            return memory_str

        # Handle no units case (assumed to be bytes)
        if memory_str.isdigit():
            return int(memory_str)

        # Extract numeric value and unit
        numeric = ""
        for char in memory_str:
            if char.isdigit() or char == ".":
                numeric += char
            else:
                break

        unit = memory_str[len(numeric) :].strip()
        value = float(numeric)

        # Convert to bytes based on unit
        if unit in ("Ki", "K"):
            return int(value * 1024)
        elif unit in ("Mi", "M"):
            return int(value * 1024 * 1024)
        elif unit in ("Gi", "G"):
            return int(value * 1024 * 1024 * 1024)
        elif unit in ("Ti", "T"):
            return int(value * 1024 * 1024 * 1024 * 1024)
        elif unit in ("Pi", "P"):
            return int(value * 1024 * 1024 * 1024 * 1024 * 1024)
        else:
            # Default to bytes if unit not recognized
            return int(value)

    def _version_satisfies(self, version, constraint, operator):
        """
        Check if a version satisfies a constraint with a given operator.

        Args:
            version: Version string
            constraint: Version constraint
            operator: Comparison operator ('>=' only implemented currently)

        Returns:
            bool: True if the version satisfies the constraint
        """
        if operator == ">=":
            # Simple implementation - split on dots and compare each part numerically
            v_parts = [int(p) if p.isdigit() else p for p in version.split(".")]
            c_parts = [int(p) if p.isdigit() else p for p in constraint.split(".")]

            # Pad shorter list with zeros
            max_len = max(len(v_parts), len(c_parts))
            v_parts = v_parts + [0] * (max_len - len(v_parts))
            c_parts = c_parts + [0] * (max_len - len(c_parts))

            # Compare each part
            for v, c in zip(v_parts, c_parts):
                if isinstance(v, int) and isinstance(c, int):
                    if v > c:
                        return True
                    if v < c:
                        return False
                else:
                    # For non-numeric parts, use string comparison
                    if str(v) > str(c):
                        return True
                    if str(v) < str(c):
                        return False

            # If we get here, versions are equal
            return True

        # Unsupported operator
        return False
