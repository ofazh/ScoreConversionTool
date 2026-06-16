from dataclasses import dataclass
from typing import List


VALID_SECTIONS = ["RW", "Math"]


@dataclass(frozen=True)
class StudentWideSchema:
    required_columns: List[str] = None

    def __post_init__(self):
        object.__setattr__(self, "required_columns", ["student_id", "theta_rw", "theta_math"])


@dataclass(frozen=True)
class StudentLongSchema:
    required_columns: List[str] = None

    def __post_init__(self):
        object.__setattr__(self, "required_columns", ["student_id", "section", "theta"])


@dataclass(frozen=True)
class ConversionSchema:
    required_columns: List[str] = None

    def __post_init__(self):
        object.__setattr__(
            self,
            "required_columns",
            ["table_name", "section", "theta_min", "theta_max", "scale_score"],
        )