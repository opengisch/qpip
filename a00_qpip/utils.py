import os
import subprocess
from collections import namedtuple
from importlib.metadata import Distribution
from subprocess import PIPE, STDOUT, Popen
from typing import List, Union

from qgis.core import Qgis, QgsApplication, QgsMessageLog
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMessageBox, QProgressDialog
from qgis.utils import iface

from .install_progress import PipInstallProgressDialog

PipInstallResult = namedtuple("PipInstallResult", ["ok", "cancelled"])


def log(message):
    QgsMessageLog.logMessage(message, "QPIP", level=Qgis.MessageLevel.Info)


def warn(message):
    QgsMessageLog.logMessage(message, "QPIP", level=Qgis.MessageLevel.Warning)


class VersionConflict(Exception):
    pass


class DistributionNotFound(Exception):
    pass


class Req:
    def __init__(self, plugin, requirement, error):
        self.plugin: str = plugin
        self.requirement: str = requirement
        self.error: Union[None, Exception] = error


class Lib:
    def __init__(self):
        self.name: str = None
        self.required_by: List[Req] = []
        self.installed_dist: Distribution = None
        self.qpip: bool = True


def icon(name):
    return QIcon(os.path.join(os.path.dirname(__file__), "icons", name))


def run_cmd(args, description="running a system command", report_errors=True) -> bool:
    """
    Runs a command, showing a progress dialog.

    Returns whether the command succeeded. Failures are reported to the user
    unless `report_errors` is False, which allows the caller to retry with
    another command before bothering the user.
    """
    progress_dlg = QProgressDialog(
        description, "Abort", 0, 0, parent=iface.mainWindow()
    )
    progress_dlg.setWindowModality(Qt.WindowModality.WindowModal)
    progress_dlg.show()

    startupinfo = None
    if os.name == "nt":
        from subprocess import (
            STARTF_USESHOWWINDOW,
            STARTF_USESTDHANDLES,
            STARTUPINFO,
            SW_HIDE,
        )

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
        warn("Command failed.")
        if report_errors:
            message = QMessageBox(
                QMessageBox.Icon.Warning,
                "Command failed",
                f"Encountered an error while {description} !",
                parent=iface.mainWindow(),
            )
            message.setDetailedText(full_output)
            message.exec()
    else:
        log("Command succeeded.")
        iface.messageBar().pushMessage(
            "Success",
            f"{description.capitalize()} succeeded",
            level=Qgis.Success,
        )

    return process.returncode == 0


def run_pip_install(args, requirements, report_errors=True) -> PipInstallResult:
    """Run one pip install command with per-dependency progress reporting."""
    requirement_label = "requirement" if len(requirements) == 1 else "requirements"
    description = f"installing {len(requirements)} {requirement_label}"
    dialog = PipInstallProgressDialog(
        args,
        requirements,
        description,
        log_callback=log,
        parent=iface.mainWindow(),
    )
    return_code, cancelled, full_output = dialog.execute()

    if return_code == 0:
        log("Command succeeded.")
        iface.messageBar().pushMessage(
            "Success",
            f"{description.capitalize()} succeeded",
            level=Qgis.Success,
        )
        return PipInstallResult(True, False)

    if cancelled:
        warn("Dependency installation was cancelled.")
        iface.messageBar().pushMessage(
            "Cancelled",
            "Dependency installation was cancelled",
            level=Qgis.Warning,
        )
        return PipInstallResult(False, True)

    warn("Command failed.")
    if report_errors:
        message = QMessageBox(
            QMessageBox.Icon.Warning,
            "Command failed",
            f"Encountered an error while {description} !",
            parent=iface.mainWindow(),
        )
        message.setDetailedText(full_output)
        message.exec()
    return PipInstallResult(False, False)
