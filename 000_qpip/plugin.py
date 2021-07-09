import os
import subprocess
import sys

import pkg_resources
from PyQt5 import uic
from qgis import utils
from qgis.core import QgsApplication, QgsMessageLog
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDialog, QTableWidgetItem


class Plugin:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self._defered_packages = []
        self._init_complete = False

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
            sys.path.append(self.site_packages_path)
        if self.bin_path not in os.environ["PATH"]:
            QgsMessageLog.logMessage(f"Adding {self.bin_path} to PATH", "Plugins")
            os.environ["PATH"] = self.bin_path + ";" + os.environ["PATH"]

        # Monkey patch qgis.utils
        QgsMessageLog.logMessage("Applying monkey patch to qgis.utils", "Plugins")
        self._initial_loadPlugin = utils.loadPlugin
        utils.loadPlugin = self.patched_load_plugin

        self.iface.initializationCompleted.connect(self.initComplete)

    def initGui(self):
        """Create the menu entries and toolbar icons inside the QGIS GUI."""
        # Add toolbar
        self.toolbar = self.iface.addToolBar("Plugins")

        self.main_action = QAction(
            QIcon(os.path.join(self.plugin_dir, "icon.svg")),
            "Run qpip",
            self.toolbar,
        )
        # self.main_action.triggered.connect(self.do_stuff)
        self.toolbar.addAction(self.main_action)

    def initComplete(self):
        QgsMessageLog.logMessage(
            f"Initialization complete. Loading {self._defered_packages=}", "Plugins"
        )
        self._init_complete = True
        for defered_package in self._defered_packages:
            self.patched_load_plugin(defered_package)
        self._defered_packages = []

    def unload(self):
        self.iface.mainWindow().removeToolBar(self.toolbar)

        # Remove monkey patch
        QgsMessageLog.logMessage("Unapplying monkey patch to qgis.utils", "Plugins")
        utils.loadPlugin = self._initial_loadPlugin

        # Remove path alterations
        if self.site_packages_path in sys.path:
            sys.path.remove(self.site_packages_path)

    def patched_load_plugin(self, packageName):
        """
        This replaces qgis.utils.loadPlugin to check if dependencies are met. If so, it just calls the
        original qgis.utils.loadPlugin. Otherwise, it will defer loading to self.deferred_loadPlugin.
        """

        requirements_path = os.path.join(
            self.plugins_path, packageName, "requirements.txt"
        )

        # If requirements.txt is present, we see if we can load it
        if os.path.isfile(requirements_path):
            QgsMessageLog.logMessage(f"{packageName} has a requirements.txt", "Plugins")

            missing_deps = {}

            with open(requirements_path, "r") as f:
                requirements = pkg_resources.parse_requirements(f)
                for requirement in requirements:
                    try:
                        pkg_resources.require(str(requirement))
                    except pkg_resources.DistributionNotFound as e:
                        missing_deps[str(requirement)] = "missing"
                    except pkg_resources.VersionConflict as e:
                        missing_deps[str(requirement)] = f"conflict ({e.dist})"

            if missing_deps:
                if not self._init_complete:
                    QgsMessageLog.logMessage(
                        f"{packageName} has missing requirements. We defer loading to after initialization",
                        "Plugins",
                    )
                    self._defered_packages.append(packageName)
                    return False

                dialog = QDialog()
                uic.loadUi(os.path.join(os.path.dirname(__file__), "dialog.ui"), dialog)

                checkboxes = {}
                dialog.tableWidget.setRowCount(len(missing_deps))
                for i, (req, state) in enumerate(missing_deps.items()):
                    checkboxes[req] = QTableWidgetItem()
                    checkboxes[req].setCheckState(Qt.Checked)
                    dialog.tableWidget.setItem(i, 0, QTableWidgetItem(packageName))
                    dialog.tableWidget.setItem(i, 1, QTableWidgetItem(req))
                    dialog.tableWidget.setItem(i, 2, QTableWidgetItem(state))
                    dialog.tableWidget.setItem(i, 3, checkboxes[req])

                dialog.exec_()

                deps_to_install = [
                    req
                    for req, checkbox in checkboxes.items()
                    if checkbox.checkState() == Qt.Checked
                ]

                if deps_to_install:
                    os.makedirs(self.prefix_path, exist_ok=True)
                    pip_args = [
                        "python",
                        "-m",
                        "pip",
                        "install",
                        *deps_to_install,
                        "--prefix",
                        self.prefix_path,
                    ]
                    QgsMessageLog.logMessage(
                        f"Running command : {pip_args=}", "Plugins"
                    )
                    subprocess.check_call(pip_args, shell=True)

        QgsMessageLog.logMessage(f"Loading {packageName}", "Plugins")
        return self._initial_loadPlugin(packageName)
