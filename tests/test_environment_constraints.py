"""
Tests for the pip constraints built from the QGIS Python environment.

These tests verify that:
- Libraries installed in the QGIS Python environment are pinned to their version
- Libraries installed by QPIP itself are not pinned, so they can be upgraded
- Libraries explicitly required by a plugin are not pinned
- Unusable distributions (no name, no version, unpinnable version) are skipped
- The constraints are passed to pip, and dropped if pip can't resolve them
"""

from unittest.mock import patch

import pytest

from a00_qpip.plugin import Plugin

SYSTEM_PATH = "/usr/lib/python3/dist-packages"


class initializationCompleted:
    def connect(self):
        pass


def popWidget():
    return True


class FakeDistribution:
    """Minimal stand-in for importlib.metadata.Distribution."""

    def __init__(self, name, version, path=None):
        self.metadata = {"Name": name, "Version": version}
        self._path = path or f"{SYSTEM_PATH}/{name}.dist-info"


def fake_distributions(*dists):
    """Patches the distributions found on sys.path, in sys.path order."""
    return patch("a00_qpip.plugin.metadata.distributions", return_value=dists)


@pytest.fixture()
def plugin(qgis_iface, tmp_path):
    """A plugin installing its dependencies in a temporary profile folder."""
    qgis_iface.initializationCompleted = initializationCompleted
    qgis_iface.messageBar().popWidget = popWidget
    with patch(
        "a00_qpip.plugin.QgsApplication.qgisSettingsDirPath",
        return_value=str(tmp_path),
    ):
        return Plugin(qgis_iface, str(tmp_path / "python" / "plugins"))


def test_pins_environment_libraries(plugin: Plugin):
    """Libraries found outside the QPIP folder are pinned to their version."""
    with fake_distributions(
        FakeDistribution("numpy", "1.24.4"),
        FakeDistribution("Pandas", "2.0.3"),
    ):
        constraints = plugin.environment_constraints([])

    assert constraints == ["numpy==1.24.4", "Pandas==2.0.3"]


def test_skips_qpip_installed_libraries(plugin: Plugin):
    """Libraries installed by QPIP are not pinned, so they can be upgraded."""
    qpip_dist = str(plugin.site_packages_path / "cowsay-4.0.dist-info")
    with fake_distributions(
        FakeDistribution("cowsay", "4.0", qpip_dist),
        FakeDistribution("numpy", "1.24.4"),
    ):
        constraints = plugin.environment_constraints([])

    assert constraints == ["numpy==1.24.4"]


def test_skips_requested_libraries(plugin: Plugin):
    """Libraries a plugin asks for are not pinned to the installed version."""
    with fake_distributions(
        FakeDistribution("cow-say", "4.0"),
        FakeDistribution("numpy", "1.24.4"),
    ):
        constraints = plugin.environment_constraints(["cow_say==5.0"])

    assert constraints == ["numpy==1.24.4"]


def test_keeps_first_of_duplicated_libraries(plugin: Plugin):
    """When a library is installed twice, the version that gets imported wins."""
    with fake_distributions(
        FakeDistribution("numpy", "1.24.4", "/first/on/sys/path/numpy.dist-info"),
        FakeDistribution("numpy", "1.21.0", "/later/on/sys/path/numpy.dist-info"),
    ):
        constraints = plugin.environment_constraints([])

    assert constraints == ["numpy==1.24.4"]


def test_skips_unusable_distributions(plugin: Plugin):
    """Distributions without metadata or with an unpinnable version are skipped."""
    with fake_distributions(
        FakeDistribution(None, "1.0", f"{SYSTEM_PATH}/unnamed.dist-info"),
        FakeDistribution("unversioned", None),
        FakeDistribution("broken", "not a version"),
        FakeDistribution("numpy", "1.24.4"),
    ):
        constraints = plugin.environment_constraints([])

    assert constraints == ["numpy==1.24.4"]


def test_install_passes_constraints_to_pip(plugin: Plugin):
    """The constraints are written to a file passed to pip."""
    with patch("a00_qpip.plugin.run_cmd", return_value=True) as run_cmd:
        plugin.run_pip_install(["cowsay==4.0"], ["numpy==1.24.4"])

    cmd = run_cmd.call_args[0][0]
    assert cmd[cmd.index("--constraint") + 1].endswith("constraints.txt")


def test_install_retries_without_constraints(plugin: Plugin):
    """If pip can't resolve the constraints, the install is retried without them."""
    with patch.object(plugin, "environment_constraints", return_value=["numpy==1.0"]):
        with patch("a00_qpip.plugin.run_cmd", return_value=False) as run_cmd:
            plugin.pip_install_reqs(["cowsay==4.0"])

    first_cmd, second_cmd = (call[0][0] for call in run_cmd.call_args_list)
    assert "--constraint" in first_cmd
    assert "--constraint" not in second_cmd


def test_install_without_environment_reports_errors(plugin: Plugin):
    """Without constraints, pip failures are reported to the user right away."""
    with patch.object(plugin, "environment_constraints", return_value=[]):
        with patch("a00_qpip.plugin.run_cmd", return_value=False) as run_cmd:
            plugin.pip_install_reqs(["cowsay==4.0"])

    run_cmd.assert_called_once()
    assert run_cmd.call_args[1]["report_errors"]
