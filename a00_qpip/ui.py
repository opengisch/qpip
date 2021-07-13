import os
from importlib import metadata

from PyQt5 import uic
from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QDialog, QTableWidgetItem


class InstallMissingDialog(QDialog):
    def __init__(self, missing_deps):
        super().__init__()
        uic.loadUi(os.path.join(os.path.dirname(__file__), "ui_install.ui"), self)

        self.checkboxes = {}
        self.tableWidget.setRowCount(len(missing_deps))
        for i, dep in enumerate(missing_deps):
            self.checkboxes[dep] = QTableWidgetItem()
            self.checkboxes[dep].setCheckState(Qt.Checked)
            self.tableWidget.setItem(i, 0, QTableWidgetItem(dep.package))
            self.tableWidget.setItem(i, 1, QTableWidgetItem(dep.requirement))
            self.tableWidget.setItem(i, 2, QTableWidgetItem(dep.state))
            self.tableWidget.setItem(i, 3, self.checkboxes[dep])

        if QgsApplication.primaryScreen().logicalDotsPerInch() > 110:
            self.setMinimumSize(self.minimumWidth() * 2, self.minimumHeight() * 2)

    def deps_to_install(self):
        deps = []
        for dep, checkbox in self.checkboxes.items():
            if checkbox.checkState() == Qt.Checked:
                deps.append(dep)
        return deps

    def deps_to_dontask(self):
        deps = []
        if self.dontAskAgainCheckbox.isChecked():
            for dep, checkbox in self.checkboxes.items():
                if checkbox.checkState() == Qt.Unchecked:
                    deps.append(dep)
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

        self.settings.beginGroup("skips")
        keys = self.settings.allKeys()
        self.settings.endGroup()

        self.tableWidget.setRowCount(len(keys))
        for i, key in enumerate(keys):
            package, req = key.split("/")
            self.tableWidget.setItem(i, 0, QTableWidgetItem(package))
            self.tableWidget.setItem(i, 1, QTableWidgetItem(req))

    def clear_skips(self):
        self.settings.remove("skips")
        self.repopulate()
