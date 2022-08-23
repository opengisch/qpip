import os
import platform
import subprocess
import sys
from collections import namedtuple
from subprocess import (
    PIPE,
    STARTF_USESHOWWINDOW,
    STARTF_USESTDHANDLES,
    STARTUPINFO,
    STDOUT,
    SW_HIDE,
    Popen,
)

import pkg_resources
import qgis
from pkg_resources import DistributionNotFound, VersionConflict
from qgis.core import Qgis, QgsApplication, QgsSettings
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox, QProgressDialog

from .log import log, warn
from .ui import InstallMissingDialog, ShowDialog, SkipDialog

MissingDep = namedtuple("MissingDep", ["package", "requirement", "state"])


class Plugin:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self._defered_packages = []
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
            log(f"Adding {self.site_packages_path} to PYTHONPATH")
            sys.path.insert(0, self.site_packages_path)
            os.environ["PYTHONPATH"] = (
                self.site_packages_path + ";" + os.environ.get("PYTHONPATH", "")
            )

        if self.bin_path not in os.environ["PATH"]:
            log(f"Adding {self.bin_path} to PATH")
            os.environ["PATH"] = self.bin_path + ";" + os.environ["PATH"]

        sys.path_importer_cache.clear()

        # Monkey patch qgis.utils
        log("Applying monkey patch to qgis.utils")
        self._original_loadPlugin = qgis.utils.loadPlugin
        qgis.utils.loadPlugin = self.patched_load_plugin

        self.iface.initializationCompleted.connect(self.initComplete)

    def initGui(self):

        icon = QIcon(os.path.join(self.plugin_dir, "icon.svg"))

        self.check_action = QAction(icon, "Run dependencies check now")
        self.check_action.triggered.connect(self.check)
        self.iface.addPluginToMenu("QPIP", self.check_action)

        self.show_action = QAction("List installed libraries")
        self.show_action.triggered.connect(self.show)
        self.iface.addPluginToMenu("QPIP", self.show_action)

        self.skip_action = QAction("Show skips")
        self.skip_action.triggered.connect(self.skip)
        self.iface.addPluginToMenu("QPIP", self.skip_action)

        self.show_folder_action = QAction("Open current profile library folder")
        self.show_folder_action.triggered.connect(self.show_folder)
        self.iface.addPluginToMenu("QPIP", self.show_folder_action)

        self.toggle_startup_action = QAction("Check dependencies on startup")
        self.toggle_startup_action.setCheckable(True)
        self.toggle_startup_action.setChecked(self._is_check_on_startup_enabled())
        self.toggle_startup_action.toggled.connect(self.toggle_startup)
        self.iface.addPluginToMenu("QPIP", self.toggle_startup_action)

    def initComplete(self):
        if self._defered_packages:
            log(f"Initialization complete. Loading deferred packages")
            self.install_deps_for_packages(self._defered_packages)
            self.start_packages(self._defered_packages)
        self._defered_packages = []

    def unload(self):
        self.iface.removePluginMenu("QPIP", self.show_action)
        self.iface.removePluginMenu("QPIP", self.skip_action)
        self.iface.removePluginMenu("QPIP", self.check_action)
        self.iface.removePluginMenu("QPIP", self.show_folder_action)
        self.iface.removePluginMenu("QPIP", self.toggle_startup_action)

        # Remove monkey patch
        log("Unapplying monkey patch to qgis.utils")
        qgis.utils.loadPlugin = self._original_loadPlugin

        # Remove path alterations
        if self.site_packages_path in sys.path:
            sys.path.remove(self.site_packages_path)
            os.environ["PYTHONPATH"] = os.environ["PYTHONPATH"].replace(
                self.bin_path + ";", ""
            )
            os.environ["PATH"] = os.environ["PATH"].replace(self.bin_path + ";", "")

    def patched_load_plugin(self, packageName):
        """
        This replaces qgis.utils.loadPlugin
        """
        if not self._is_qgis_loaded():
            if not self._is_check_on_startup_enabled():
                # During QGIS startup, with initial loading disabled, we simply load the plugin
                log(f"Check disabled. Normal loading of {packageName}.")
                return self._original_loadPlugin(packageName)
            else:
                # During QGIS startup, with initial loading enabled, we defer loading
                log(f"GUI not ready. Deferring loading of {packageName}.")
                self._defered_packages.append(packageName)
                return False
        else:
            # QGIS ready, we install right away (probably a plugin that was just enabled)
            log(f"GUI ready. Insalling deps then loading {packageName}.")
            self.install_deps_for_packages([packageName])
            self.start_packages([packageName])
            return True

    def install_deps_for_packages(self, packageNames):
        """
        This collects all missing deps for given packages, then shows a GUI to install them.
        """

        log(f"Installing deps for {packageNames} before starting them.")

        missing_deps = []
        for packageName in packageNames:
            missing_deps.extend(self.list_missing_deps(packageName))

        deps_to_install = []
        log(f"{len(missing_deps)} missing dependencies.")
        if len(missing_deps):

            dialog = InstallMissingDialog(missing_deps)
            if dialog.exec_():
                deps_to_install = dialog.deps_to_install()
                deps_to_dontask = dialog.deps_to_dontask()

                for dep in deps_to_dontask:
                    self.settings.setValue(
                        f"skips/{dep.package}/{dep.requirement}", True
                    )

        if deps_to_install:
            log(f"Will install selected dependencies : {deps_to_install}")
            self.pip_install_deps(deps_to_install)

            sys.path_importer_cache.clear()

    def start_packages(self, packageNames):
        """
        This loads and starts all packages.

        It tries to match implementation of QgsPluginRegistry::loadPythonPlugin (including watchdog).
        """

        for packageName in packageNames:
            log(f"Proceeding to load {packageName}")
            could_load = self._original_loadPlugin(packageName)

            if could_load:
                # When called deferred, we also need to start the plugin. This matches implementation
                # of QgsPluginRegistry::loadPythonPlugin
                could_start = qgis.utils.startPlugin(packageName)
                if could_start:
                    QgsSettings().setValue("/PythonPlugins/" + packageName, True)
                    QgsSettings().remove("/PythonPlugins/watchDog/" + packageName)

    def list_missing_deps(self, packageName):
        missing_deps = []
        # If requirements.txt is present, we see if we can load it
        requirements_path = os.path.join(
            self.plugins_path, packageName, "requirements.txt"
        )
        if os.path.isfile(requirements_path):
            log(f"Loading requirements for {packageName}")

            with open(requirements_path, "r") as f:
                requirements = pkg_resources.parse_requirements(f)
                working_set = pkg_resources.WorkingSet()
                for requirement in requirements:
                    req = str(requirement)
                    if self.settings.value(f"skips/{packageName}/{req}", False):
                        log(f"Skipping {req} required by {packageName}.")
                        continue

                    try:
                        working_set.require(req)
                    except (DistributionNotFound, VersionConflict) as e:
                        if isinstance(e, DistributionNotFound):
                            error = "missing"
                        else:  # if isinstance(e, VersionConflict):
                            error = f"conflict ({e.dist})"
                        missing_deps.append(MissingDep(packageName, req, error))
        return missing_deps

    def pip_install_deps(self, deps_to_install, extra_args=[]):
        """
        Installs given deps with pip
        """
        os.makedirs(self.prefix_path, exist_ok=True)
        reqs = [dep.requirement for dep in deps_to_install]
        log(f"Will pip install {reqs}")
        pip_args = [
            "python",
            "-um",
            "pip",
            "install",
            *reqs,
            "--prefix",
            self.prefix_path,
            *extra_args,
        ]

        progress_dlg = QProgressDialog(
            "Installing dependencies", "Abort", 0, 0, parent=self.iface.mainWindow()
        )
        progress_dlg.setWindowModality(Qt.WindowModal)
        progress_dlg.show()

        startupinfo = None
        if os.name == "nt":
            startupinfo = STARTUPINFO()
            startupinfo.dwFlags |= STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = SW_HIDE

        process = Popen(pip_args, stdout=PIPE, stderr=STDOUT, startupinfo=startupinfo)

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
            warn(f"Installation failed.")
            message = QMessageBox(
                QMessageBox.Warning,
                "Installation failed",
                f"Installation of dependecies failed (code {process.returncode}).\nSee logs for more information.",
                parent=self.iface.mainWindow(),
            )
            message.setDetailedText(full_output)
            message.exec_()
        else:
            log("Installation succeeded.")
            self.iface.messageBar().pushMessage(
                "Success",
                f"Installed {len(deps_to_install)} requirements",
                level=Qgis.Success,
            )

    def show(self):
        dialog = ShowDialog()
        dialog.exec_()

    def skip(self):
        dialog = SkipDialog()
        dialog.exec_()

    def check(self):
        self.install_deps_for_packages(qgis.utils.active_plugins)

    def toggle_startup(self, toggled):
        # seems QgsSettings doesn't deal well with bools !!
        self.settings.setValue("check_on_startup", "yes" if toggled else "no")

    def show_folder(self):
        if platform.system() == "Windows":
            os.startfile(self.prefix_path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", self.prefix_path])
        else:
            subprocess.Popen(["xdg-open", self.prefix_path])

    def _is_check_on_startup_enabled(self):
        return self.settings.value("check_on_startup", "yes") == "yes"

    def _is_qgis_loaded(self):
        return self.iface.mainWindow().isVisible()
