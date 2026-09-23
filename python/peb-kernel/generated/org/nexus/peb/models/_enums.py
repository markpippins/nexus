# coding=utf-8

from enum import Enum
from corehttp.utils import CaseInsensitiveEnumMeta


class AdmissionResult(str, Enum, metaclass=CaseInsensitiveEnumMeta):
    """Result of an admission check."""

    ALLOWED = "ALLOWED"
    """ALLOWED."""
    REJECTED = "REJECTED"
    """REJECTED."""
    ROUTED = "ROUTED"
    """ROUTED."""
