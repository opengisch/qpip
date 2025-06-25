import os
from typing import Dict, List

from pkg_resources import DistributionNotFound, VersionConflict
from qgis.PyQt import uic
from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QComboBox, QDialog, QTableWidgetItem

from .utils import Lib, icon


class MainDialog(QDialog):
    def __init__(self, libs: List[Lib], check_on_startup, check_on_install):
        super().__init__()
        uic.loadUi(os.path.join(os.path.dirname(__file__), "ui_dialog.ui"), self)

        self.libs = libs

        self.startup_checkbox.setChecked(check_on_startup)
        self.install_checkbox.setChecked(check_on_install)

        self.action_combos: Dict[Lib, QComboBox] = {}
        self.table_widget.setRowCount(0)
        for i, lib in enumerate(self.libs):
            # Add row
            self.table_widget.insertRow(self.table_widget.rowCount())

            def make_widget(label, tooltip=None):
                item = QTableWidgetItem(label)
                item.setToolTip(tooltip)
                try:
                    # Qt6
                    item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                except AttributeError:
                    # Qt5
                    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                return item

            def make_error_widget(error_type):
                label = []
                tooltip = []
                for req in lib.required_by:
                    if isinstance(req.error, error_type):
                        label.append(req.plugin)
                        tooltip.append(f"{req.requirement} [by {req.plugin}]")
                return make_widget(" ".join(label), "\n".join(tooltip))

            # library
            self.table_widget.setItem(i, 0, make_widget(lib.name))

            # installed
            if lib.installed_dist:
                label = lib.installed_dist.version
                tooltip = f"Installed in {lib.installed_dist._path}"
            else:
                label = "-"
                tooltip = "Not installed"
            if not lib.qpip:
                label += " [global]"
            self.table_widget.setItem(i, 1, make_widget(label, tooltip))

            # ok
            self.table_widget.setItem(i, 2, make_error_widget(type(None)))

            # conflicting
            self.table_widget.setItem(i, 3, make_error_widget(VersionConflict))

            # missing
            self.table_widget.setItem(i, 4, make_error_widget(DistributionNotFound))

            # actions
            action_combo = QComboBox()
            action_combo.addItem("Do nothing")
            for req in lib.required_by:
                action_combo.addItem(
                    icon("qpip.svg"),
                    f"Install {req.requirement}",
                    ("install", req.requirement),
                )
            if lib.installed_dist != None:
                action_combo.addItem(
                    icon("uninstall.svg"), "Uninstall", ("uninstall", lib.name)
                )
            self.table_widget.setCellWidget(i, 5, action_combo)
            self.action_combos[lib] = action_combo

            # row color (gray out system deps)
            row_color = (
                QgsApplication.palette().base().color()
                if lib.qpip
                else QColor("#aaaaaa")
            )
            for j in range(0, 5):
                self.table_widget.item(i, j).setBackground(row_color)

            # cell colors (red/orange/green depending on errors)
            if any(isinstance(req.error, VersionConflict) for req in lib.required_by):
                color = QColor("#f7e463")
            elif any(
                isinstance(req.error, DistributionNotFound) for req in lib.required_by
            ):
                color = QColor("#eb6060")
            elif lib.required_by:
                color = QColor("#7cd992")
            else:
                color = None
            if color:
                for j in range(2, 5):
                    self.table_widget.item(i, j).setBackground(color)

        self.table_widget.resizeColumnsToContents()

        self._default_all()

        if QgsApplication.primaryScreen().logicalDotsPerInch() > 110:
            self.setMinimumSize(self.minimumWidth() * 2, self.minimumHeight() * 2)

        self.filter_combobox.addItem("Missing or conflicting only")
        self.filter_combobox.addItem("Required by plugins")
        self.filter_combobox.addItem("Show all")
        self.filter_combobox.addItem("Show all incl. system deps [not recommended]")
        self.filter_combobox.setCurrentIndex(1)

        self.filter_combobox.currentIndexChanged.connect(self._filter)
        self.ignore_button.pressed.connect(self._ignore_all)
        self.default_button.pressed.connect(self._default_all)

        self._filter()

    def _filter(self):
        for i, lib in enumerate(self.libs):
            self.table_widget.showRow(i)
            if self.action_combos[lib].currentIndex() != 0:
                # Never skip libraries with selected actions
                pass
            elif self.filter_combobox.currentIndex() == 0:
                # Skip libraries without requirement issue
                if all(req.error is None for req in lib.required_by):
                    self.table_widget.hideRow(i)
            elif self.filter_combobox.currentIndex() == 1:
                # Skip libraries not required by plugins
                if not lib.required_by:
                    self.table_widget.hideRow(i)
            elif self.filter_combobox.currentIndex() == 2:
                # Skip libraries installed globally
                if not lib.qpip:
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

    @property
    def reqs_to_install(self):
        return list(self._selected_actions("install"))

    @property
    def reqs_to_uninstall(self):
        return list(self._selected_actions("uninstall"))

    @property
    def check_on_startup(self):
        return self.startup_checkbox.isChecked()

    @property
    def check_on_install(self):
        return self.install_checkbox.isChecked()
