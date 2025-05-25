from __future__ import annotations


class WorkflowRequests:
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
    def with_memory(cls, memory: str | int) -> WorkflowRequests:
        """
        Create a WorkflowRequests object with specified memory.

        Args:
            memory (str | int): The memory requirement.

        Returns:
            WorkflowRequests: A new instance with the specified memory.
        """
        return cls(memory=memory)

    @classmethod
    def with_cpu(cls, cpu: int) -> WorkflowRequests:
        """
        Create a WorkflowRequests object with specified CPU.

        Args:
            cpu (int): The CPU requirement.

        Returns:
            WorkflowRequests: A new instance with the specified CPU.
        """
        return cls(cpu=cpu)

    @classmethod
    def with_disk(cls, disk: int) -> WorkflowRequests:
        """
        Create a WorkflowRequests object with specified disk.

        Args:
            disk (int): The disk requirement.

        Returns:
            WorkflowRequests: A new instance with the specified disk.
        """
        return cls(disk=disk)

    @classmethod
    def with_gpu(cls, gpu: int) -> WorkflowRequests:
        """
        Create a WorkflowRequests object with specified GPU.

        Args:
            gpu (int): The GPU requirement.

        Returns:
            WorkflowRequests: A new instance with the specified GPU.
        """
        return cls(gpu=gpu)

    @classmethod
    def with_packages(cls, packages: list[str]) -> WorkflowRequests:
        """
        Create a WorkflowRequests object with specified packages.

        Args:
            packages (list[str]): The list of required packages.

        Returns:
            WorkflowRequests: A new instance with the specified packages.
        """
        return cls(packages=packages)
