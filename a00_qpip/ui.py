import os
from typing import Dict, List

from pkg_resources import DistributionNotFound, VersionConflict
from PyQt5 import uic
from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QComboBox, QDialog, QTableWidgetItem

from .utils import Lib


class MainDialog(QDialog):
    def __init__(self, libs: List[Lib]):
        super().__init__()
        uic.loadUi(os.path.join(os.path.dirname(__file__), "ui_dialog.ui"), self)

        self.libs = libs

        self.action_combos: Dict[Lib, QComboBox] = {}
        self.table_widget.setRowCount(0)
        for i, lib in enumerate(self.libs):

            # Add row
            self.table_widget.insertRow(self.table_widget.rowCount())

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
            self.table_widget.setItem(i, 0, QTableWidgetItem(lib.name))

            # ok
            self.table_widget.setItem(i, 1, make_widget(type(None)))

            # conflicting
            self.table_widget.setItem(i, 2, make_widget(VersionConflict))

            # missing
            self.table_widget.setItem(i, 3, make_widget(DistributionNotFound))

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
            self.table_widget.setItem(i, 4, widget)

            # actions
            action_combo = QComboBox()
            action_combo.addItem("Do nothing")
            for req in lib.required_by:
                action_combo.addItem(
                    f"Install {req.requirement}", ("install", req.requirement)
                )
            if lib.installed_dist != None:
                action_combo.addItem("Uninstall", ("uninstall", lib.name))
            self.table_widget.setCellWidget(i, 5, action_combo)
            self.action_combos[lib] = action_combo
        self.table_widget.resizeColumnsToContents()

        self._default_all()

        if QgsApplication.primaryScreen().logicalDotsPerInch() > 110:
            self.setMinimumSize(self.minimumWidth() * 2, self.minimumHeight() * 2)

        self.filter_combobox.addItem("Missing and conflicting")
        self.filter_combobox.addItem("Required by plugins")
        self.filter_combobox.addItem("Show everything")

        self.filter_combobox.currentIndexChanged.connect(self._filter)
        self.ignore_button.pressed.connect(self._ignore_all)
        self.default_button.pressed.connect(self._default_all)

        self._filter()

    def _filter(self):
        for i, lib in enumerate(self.libs):
            self.table_widget.showRow(i)
            if self.action_combos[lib].currentIndex() != 0:
                # Never skip libraries with selection actions
                pass
            elif self.filter_combobox.currentIndex() == 0:
                # Skip libraries without requirement issue
                if all(req.error is None for req in lib.required_by):
                    self.table_widget.hideRow(i)
            elif self.filter_combobox.currentIndex() == 1:
                # Skip libraries not required by plugins
                if not lib.required_by:
                    self.table_widget.hideRow(i)

    def _ignore_all(self):
        for action_combo in self.action_combos.values():
            action_combo.setCurrentIndex(0)

    def _default_all(self):
        # If there is exactly one install candidate, preselect it
        for lib, action_combo in self.action_combos.items():
            if (
                lib.qpip
                and len(lib.required_by) == 1
                and lib.required_by[0].error is not None
                and lib.installed_dist is None
            ):
                action_combo.setCurrentIndex(1)
            else:
                action_combo.setCurrentIndex(0)

    def _selected_actions(self, action_type):
        for actionCombo in self.action_combos.values():
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
