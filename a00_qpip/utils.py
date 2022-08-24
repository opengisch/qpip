import os
import subprocess
from importlib.metadata import Distribution
from subprocess import (
    PIPE,
    STARTF_USESHOWWINDOW,
    STARTF_USESTDHANDLES,
    STARTUPINFO,
    STDOUT,
    SW_HIDE,
    Popen,
)
from typing import List, Union

from pkg_resources import ResolutionError
from qgis.core import Qgis, QgsApplication, QgsMessageLog
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMessageBox, QProgressDialog
from qgis.utils import iface


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


def run_cmd(args, description="running a system command"):

    progress_dlg = QProgressDialog(
        description, "Abort", 0, 0, parent=iface.mainWindow()
    )
    progress_dlg.setWindowModality(Qt.WindowModal)
    progress_dlg.show()

    startupinfo = None
    if os.name == "nt":
        startupinfo = STARTUPINFO()
        startupinfo.dwFlags |= STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = SW_HIDE

    process = Popen(args, stdout=PIPE, stderr=STDOUT, startupinfo=startupinfo)

    full_output = ""
    while True:
        QgsApplication.processEvents()
        try:
            # FIXME : this doesn't seem to timeout
            out, _ = process.communicate(timeout=0.1)
            output = out.decode(errors="replace").strip()
            full_output += output
            if output:
                progress_dlg.setLabelText(output)
                log(output)
        except subprocess.TimeoutExpired:
            pass

        if progress_dlg.wasCanceled():
            process.kill()
        if process.poll() is not None:
            break

    progress_dlg.close()

    if process.returncode != 0:
        warn(f"Command failed.")
        message = QMessageBox(
            QMessageBox.Warning,
            "Command failed",
            f"Encountered an error while {description} !",
            parent=iface.mainWindow(),
        )
        message.setDetailedText(full_output)
        message.exec_()
    else:
        log("Command succeeded.")
        iface.messageBar().pushMessage(
            "Success",
            f"{description.capitalize()} succeeded",
            level=Qgis.Success,
        )
