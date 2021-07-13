import os
import subprocess
import sys
from importlib import metadata
from subprocess import PIPE, STDOUT, Popen

import pkg_resources
from PyQt5 import uic
from qgis import utils
from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsSettings
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction,
    QDialog,
    QMessageBox,
    QProgressDialog,
    QTableWidgetItem,
)


class Plugin:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self._defered_packages = []
        self._init_complete = False
        self.settings = QgsSettings()
        self.settings.beginGroup("QPIP")

        self.plugins_path = os.path.join(
            QgsApplication.qgisSettingsDirPath(), "python", "plugins"
        )
        self.prefix_path = os.path.join(
            QgsApplication.qgisSettingsDirPath().replace("/", os.path.sep),
            "python",
            "dependencies",
        )
        self.site_packages_path = os.path.join(self.prefix_path, "Lib", "site-packages")
        self.bin_path = os.path.join(self.prefix_path, "Scripts")

        if self.site_packages_path not in sys.path:
            QgsMessageLog.logMessage(
                f"Adding {self.site_packages_path} to PYTHONPATH", "Plugins"
            )
            sys.path.insert(0, self.site_packages_path)
        if self.bin_path not in os.environ["PATH"]:
            QgsMessageLog.logMessage(f"Adding {self.bin_path} to PATH", "Plugins")
            os.environ["PATH"] = self.bin_path + ";" + os.environ["PATH"]

        sys.path_importer_cache.clear()

        # Monkey patch qgis.utils
        QgsMessageLog.logMessage("Applying monkey patch to qgis.utils", "Plugins")
        self._initial_loadPlugin = utils.loadPlugin
        utils.loadPlugin = self.patched_load_plugin

        self.iface.initializationCompleted.connect(self.initComplete)

    def initGui(self):
        self.show_action = QAction(
            QIcon(os.path.join(self.plugin_dir, "icon.svg")), "Show installed"
        )
        self.show_action.triggered.connect(self.show)
        self.iface.addPluginToMenu("Python dependencies (QPIP)", self.show_action)

        self.skip_action = QAction(
            QIcon(os.path.join(self.plugin_dir, "icon.svg")), "Show skips"
        )
        self.skip_action.triggered.connect(self.skip)
        self.iface.addPluginToMenu("Python dependencies (QPIP)", self.skip_action)

    def initComplete(self):
        self._init_complete = True
        if self._defered_packages:
            QgsMessageLog.logMessage(
                f"Initialization complete. Loading deferred packages", "Plugins"
            )
            for defered_package in self._defered_packages:
                self.patched_load_plugin(defered_package, also_start=True)
        self._defered_packages = []

    def unload(self):
        self.iface.removePluginMenu("Python dependencies (QPIP)", self.show_action)

        # Remove monkey patch
        QgsMessageLog.logMessage("Unapplying monkey patch to qgis.utils", "Plugins")
        utils.loadPlugin = self._initial_loadPlugin

        # Remove path alterations
        if self.site_packages_path in sys.path:
            sys.path.remove(self.site_packages_path)

    def patched_load_plugin(self, packageName, also_start=False):
        """
        This replaces qgis.utils.loadPlugin to check if dependencies are met. If so, it just calls the
        original qgis.utils.loadPlugin. Otherwise, it will defer loading to self.deferred_loadPlugin
        and return False.

        When called subsequently (not by QgsPluginRegistry::loadPythonPlugin), set also_start to True,
        so that this also starts the plugin (by matching implementation of QgsPluginRegistry::loadPythonPlugin,
        not exposed to python).
        """

        missing_deps = {}

        # If requirements.txt is present, we see if we can load it
        requirements_path = os.path.join(
            self.plugins_path, packageName, "requirements.txt"
        )
        if os.path.isfile(requirements_path):
            QgsMessageLog.logMessage(
                f"Loading requirements for {packageName}", "Plugins"
            )

            with open(requirements_path, "r") as f:
                requirements = pkg_resources.parse_requirements(f)
                working_set = pkg_resources.WorkingSet()
                for requirement in requirements:
                    req = str(requirement)
                    if self.settings.value(f"skips/{packageName}/{req}", False):
                        QgsMessageLog.logMessage(
                            f"Skipping {req} required by {packageName}.", "Plugins"
                        )
                        continue
                    try:
                        working_set.require(req)
                    except pkg_resources.DistributionNotFound as e:
                        missing_deps[req] = "missing"
                    except pkg_resources.VersionConflict as e:
                        missing_deps[req] = f"conflict ({e.dist})"

        deps_to_install = []
        if missing_deps:
            QgsMessageLog.logMessage(
                f"{packageName} has missing dependencies.", "Plugins"
            )
            if not self._init_complete:
                QgsMessageLog.logMessage(
                    f"Deferring loading of {packageName} to after initialization",
                    "Plugins",
                )
                self._defered_packages.append(packageName)
                return False

            dialog = InstallMissingDialog(packageName, missing_deps)
            if dialog.exec_():
                deps_to_install = dialog.deps_to_install()
                deps_to_dontask = dialog.deps_to_dontask()

                for dep in deps_to_dontask:
                    self.settings.setValue(f"skips/{packageName}/{dep}", True)

        if deps_to_install:
            QgsMessageLog.logMessage(
                f"Will install selected dependencies : {deps_to_install}", "Plugins"
            )
            self.install_deps(deps_to_install)
            self.iface.messageBar().pushMessage(
                "Success", "Installed " + " ".join(deps_to_install), level=Qgis.Success
            )

            sys.path_importer_cache.clear()

        QgsMessageLog.logMessage(f"Proceeding to load {packageName}", "Plugins")
        could_load = self._initial_loadPlugin(packageName)

        if could_load and also_start:
            # When called deferred, we also need to start the plugin. This matches implementation
            # of QgsPluginRegistry::loadPythonPlugin
            if utils.startPlugin(packageName):
                utils.pluginMetadata(packageName, "name")
                QgsSettings().setValue("/PythonPlugins/" + packageName, True)
                QgsSettings().remove("/PythonPlugins/watchDog/" + packageName)

        return could_load

    def install_deps(self, deps_to_install, extra_args=[]):
        os.makedirs(self.prefix_path, exist_ok=True)
        pip_args = [
            "python",
            "-um",
            "pip",
            "install",
            *deps_to_install,
            "--prefix",
            self.prefix_path,
            *extra_args,
        ]

        progress_dlg = QProgressDialog(
            "Installing dependencies", "Abort", 0, 0, parent=self.iface.mainWindow()
        )
        progress_dlg.setWindowModality(Qt.WindowModal)
        progress_dlg.show()

        process = Popen(pip_args, shell=True, stdout=PIPE, stderr=STDOUT)

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
                    QgsMessageLog.logMessage(output, "Plugins")
            except subprocess.TimeoutExpired:
                pass

            if progress_dlg.wasCanceled():
                process.kill()
            if process.poll() is not None:
                break

        progress_dlg.close()

        if process.returncode != 0:
            QgsMessageLog.logMessage(f"Installation failed.", "Plugins")
            message = QMessageBox(
                QMessageBox.Warning,
                "Installation failed",
                f"Installation of dependecies failed (code {process.returncode}).\nSee logs for more information.",
                parent=self.iface.mainWindow(),
            )
            message.setDetailedText(full_output)
            message.exec_()

    def show(self):
        dialog = ShowDialog()
        dialog.exec_()

    def skip(self):
        dialog = SkipDialog()
        dialog.exec_()

    def log(self, msg):

        QgsMessageLog.logMessage(msg, "Plugins")


class InstallMissingDialog(QDialog):
    def __init__(self, package_name, missing_deps):
        super().__init__()
        uic.loadUi(os.path.join(os.path.dirname(__file__), "ui_install.ui"), self)

        self.checkboxes = {}
        self.tableWidget.setRowCount(len(missing_deps))
        for i, (req, state) in enumerate(missing_deps.items()):
            self.checkboxes[req] = QTableWidgetItem()
            self.checkboxes[req].setCheckState(Qt.Checked)
            self.tableWidget.setItem(i, 0, QTableWidgetItem(package_name))
            self.tableWidget.setItem(i, 1, QTableWidgetItem(req))
            self.tableWidget.setItem(i, 2, QTableWidgetItem(state))
            self.tableWidget.setItem(i, 3, self.checkboxes[req])

        if QgsApplication.primaryScreen().logicalDotsPerInch() > 110:
            self.setMinimumSize(self.minimumWidth() * 2, self.minimumHeight() * 2)

    def deps_to_install(self):
        deps = []
        for req, checkbox in self.checkboxes.items():
            if checkbox.checkState() == Qt.Checked:
                deps.append(req)
        return deps

    def deps_to_dontask(self):
        deps = []
        if self.dontAskAgainCheckbox.isChecked():
            for req, checkbox in self.checkboxes.items():
                if checkbox.checkState() == Qt.Unchecked:
                    deps.append(req)
        return deps


class ShowDialog(QDialog):
    def __init__(self):
        super().__init__()
        uic.loadUi(os.path.join(os.path.dirname(__file__), "ui_show.ui"), self)

        distributions = list(metadata.distributions())

        self.checkboxes = {}
        self.tableWidget.setRowCount(len(distributions))
        for i, dist in enumerate(distributions):
            self.tableWidget.setItem(i, 0, QTableWidgetItem(dist.metadata["Name"]))
            self.tableWidget.setItem(i, 1, QTableWidgetItem(dist.metadata["Version"]))
            self.tableWidget.setItem(
                i, 2, QTableWidgetItem(os.path.dirname(dist._path))
            )

        if QgsApplication.primaryScreen().logicalDotsPerInch() > 110:
            self.setMinimumSize(self.minimumWidth() * 2, self.minimumHeight() * 2)


class SkipDialog(QDialog):
    def __init__(self):
        super().__init__()
        uic.loadUi(os.path.join(os.path.dirname(__file__), "ui_skips.ui"), self)

        self.settings = QgsSettings()
        self.settings.beginGroup("QPIP")

        self.pushButton.pressed.connect(self.clear_skips)

        self.repopulate()

        if QgsApplication.primaryScreen().logicalDotsPerInch() > 110:
            self.setMinimumSize(self.minimumWidth() * 2, self.minimumHeight() * 2)

    def repopulate(self):

        keys = self.settings.allKeys()

        self.tableWidget.setRowCount(len(keys))
        for i, key in enumerate(keys):
            _, package, req = key.split("/")
            self.tableWidget.setItem(i, 0, QTableWidgetItem(package))
            self.tableWidget.setItem(i, 1, QTableWidgetItem(req))

    def clear_skips(self):
        self.settings.remove("skips")
        self.repopulate()
