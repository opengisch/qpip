from dataclasses import dataclass, field
from importlib.metadata import Distribution
from typing import List


@dataclass
class Req:
    plugin: str = None
    requirement: str = None
    error = None


@dataclass
class Lib:
    required_by: List[Req] = field(default_factory=list)
    installed_dist: Distribution = None
    qpip: bool = True
