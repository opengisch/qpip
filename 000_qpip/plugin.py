import os.path

import pkg_resources
from qgis import utils
from qgis.core import QgsApplication, QgsMessageLog
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QCheckBox, QDialog, QGridLayout, QLabel


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
        self.pip_deps_path = os.path.join(
            QgsApplication.qgisSettingsDirPath(), "python", "pip_deps"
        )

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

            missing = {}

            requirements = pkg_resources.parse_requirements(
                open(requirements_path, "r")
            )
            for requirement in requirements:
                try:
                    pkg_resources.require(str(requirement))
                except pkg_resources.DistributionNotFound:
                    missing[str(requirement)] = "missing"
                except pkg_resources.VersionConflict:
                    missing[str(requirement)] = "conflict"

            if missing:
                if not self._init_complete:
                    QgsMessageLog.logMessage(
                        f"{packageName} has missing requirements. We defer loading to after initialization",
                        "Plugins",
                    )
                    self._defered_packages.append(packageName)
                    return False

                layout = QGridLayout()
                for req, state in missing.items():
                    row = layout.rowCount()
                    layout.addWidget(QLabel(req), row, 0)
                    layout.addWidget(QLabel(state), row, 1)
                    layout.addWidget(QCheckBox(), row, 2)
                dialog = QDialog()
                dialog.setLayout(layout)

                dialog.exec_()

        QgsMessageLog.logMessage(f"Loading {packageName}", "Plugins")
        return self._initial_loadPlugin(packageName)
