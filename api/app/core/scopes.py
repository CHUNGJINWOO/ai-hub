from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class ProjectScope:
    """Explicit scope targeting a single project by ID."""

    project_id: int

    def __post_init__(self) -> None:
        if self.project_id is None:
            raise ValueError("project_id must not be None")
        if not isinstance(self.project_id, int):
            raise TypeError(
                f"project_id must be an integer, got {type(self.project_id).__name__}"
            )


@dataclass(frozen=True)
class GlobalScope:
    """Explicit scope targeting global resources unlinked to any project."""

    pass


ResourceScope = Union[ProjectScope, GlobalScope]
