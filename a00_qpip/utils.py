import os
from importlib.metadata import Distribution
from typing import List, Union

from pkg_resources import ResolutionError
from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtGui import QIcon


def log(message):
    QgsMessageLog.logMessage(message, "QPIP", level=Qgis.MessageLevel.Info)


def warn(message):
    QgsMessageLog.logMessage(message, "QPIP", level=Qgis.MessageLevel.Warning)


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


def icon(name):
    return QIcon(os.path.join(os.path.dirname(__file__), "icons", name))
