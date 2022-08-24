from importlib.metadata import Distribution
from typing import List, Union

from pkg_resources import ResolutionError


class Req:
    def __init__(self, plugin, requirement, error):
        self.plugin: str = plugin
        self.requirement: str = requirement
        self.error: Union[None, ResolutionError] = error


class Lib:
    def __init__(self):
        self.name: str = None
        self.required_by: List[Req] = []
        self.installed_dist: Distribution = None
        self.qpip: bool = True
