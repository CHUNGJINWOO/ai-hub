from enum import Enum


class Capability(str, Enum):
    """Fine-grained system capabilities decoupled from project scopes."""

    RESOURCE_READ_GLOBAL = "RESOURCE_READ_GLOBAL"
    RESOURCE_WRITE_GLOBAL = "RESOURCE_WRITE_GLOBAL"
    PROJECT_READ_ALL = "PROJECT_READ_ALL"
    PROJECT_CREATE = "PROJECT_CREATE"
    PROJECT_ADMIN = "PROJECT_ADMIN"
