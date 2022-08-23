import os
from typing import Dict

from pkg_resources import DistributionNotFound, VersionConflict
from PyQt5 import uic
from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QComboBox, QDialog, QTableWidgetItem

from .utils import Lib


class MainDialog(QDialog):
    def __init__(self, libs: Dict[str, Lib]):
        super().__init__()
        uic.loadUi(os.path.join(os.path.dirname(__file__), "ui_dialog.ui"), self)

        self.actionsCombos = []

        self.tableWidget.setRowCount(len(libs))
        for i, (name, lib) in enumerate(libs.items()):

            def make_widget(error_type):
                label = []
                tooltip = []
                for req in lib.required_by:
                    if isinstance(req.error, error_type):
                        label.append(req.plugin)
                        tooltip.append(f"{req.plugin} requires {req.requirement}")
                item = QTableWidgetItem("\n".join(label))
                item.setToolTip("\n".join(tooltip))
                return item

            # library
            self.tableWidget.setItem(i, 0, QTableWidgetItem(name))

            # ok
            self.tableWidget.setItem(i, 1, make_widget(type(None)))

            # conflicting
            self.tableWidget.setItem(i, 2, make_widget(VersionConflict))

            # missing
            self.tableWidget.setItem(i, 3, make_widget(DistributionNotFound))

            # installed
            if lib.installed_dist:
                label = lib.installed_dist.version
                tooltip = f"Installed in {lib.installed_dist._path}"
            else:
                label = "-"
                tooltip = "Not installed"
            if not lib.qpip:
                label += " [global]"
            widget = QTableWidgetItem(label)
            widget.setToolTip(tooltip)
            self.tableWidget.setItem(i, 4, widget)

            # actions
            actionCombo = QComboBox()
            actionCombo.addItem("Do nothing")
            # actionCombo.addItem("Skip", ("skip", (req.plugin, req.requirement)))
            for req in lib.required_by:
                actionCombo.addItem(
                    f"Install {req.requirement}", ("install", req.requirement)
                )
            actionCombo.addItem("Uninstall", ("uninstall", name))
            if not lib.qpip:
                actionCombo.setEnabled(False)
            self.tableWidget.setCellWidget(i, 5, actionCombo)
            self.actionsCombos.append(actionCombo)

        self.tableWidget.resizeColumnsToContents()

        if QgsApplication.primaryScreen().logicalDotsPerInch() > 110:
            self.setMinimumSize(self.minimumWidth() * 2, self.minimumHeight() * 2)

    def _selected_actions(self, action_type):
        for actionCombo in self.actionsCombos:
            data = actionCombo.currentData()
            if data is not None:
                if data[0] == action_type:
                    yield data[1]

    def deps_to_install(self):
        return list(self._selected_actions("install"))

    def deps_to_uninstall(self):
        return list(self._selected_actions("uninstall"))

    def deps_to_skip(self):
        return list(self._selected_actions("skip"))
