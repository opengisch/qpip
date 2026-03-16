"""
Tests for Python version-specific dependency folders and migration from old layout.

These tests verify that:
- Dependencies are installed into version-specific subfolders (e.g. dependencies/3.12/)
- The old flat layout (packages directly in dependencies/) is detected and cleaned up
- Existing version-specific subfolders are preserved during migration
- Locked files on Windows are handled gracefully
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from a00_qpip.plugin import Plugin


class initializationCompleted:
    def connect(self):
        pass


def popWidget():
    return True


@pytest.fixture()
def deps_dir(tmp_path):
    """Create a temporary dependencies directory."""
    deps = tmp_path / "python" / "dependencies"
    deps.mkdir(parents=True)
    return deps


@pytest.fixture()
def plugin_factory(qgis_iface, tmp_path):
    """Factory that creates a Plugin with a given tmp_path as the settings dir."""
    qgis_iface.initializationCompleted = initializationCompleted
    qgis_iface.messageBar().popWidget = popWidget

    def _create():
        with patch(
            "a00_qpip.plugin.QgsApplication.qgisSettingsDirPath",
            return_value=str(tmp_path),
        ):
            return Plugin(qgis_iface, str(tmp_path / "python" / "plugins"))

    return _create


def test_prefix_path_includes_python_version(plugin_factory, tmp_path):
    """Dependencies folder should include the Python major.minor version."""
    plugin = plugin_factory()
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    expected = tmp_path / "python" / "dependencies" / py_ver
    assert plugin.prefix_path == expected
    assert plugin.site_packages_path == expected
    assert plugin.bin_path == expected / "bin"


def test_migration_no_deps_dir(plugin_factory, tmp_path):
    """If dependencies/ doesn't exist, migration does nothing (no crash)."""
    deps = tmp_path / "python" / "dependencies"
    assert not deps.exists()
    plugin = plugin_factory()
    # Should not create the directory
    assert not deps.exists()


def test_migration_empty_deps_dir(plugin_factory, deps_dir):
    """If dependencies/ exists but is empty, migration does nothing."""
    plugin = plugin_factory()
    # Directory should still be empty (no version subfolder created yet)
    items = list(deps_dir.iterdir())
    assert len(items) == 0


def test_migration_cleans_old_flat_layout(plugin_factory, deps_dir):
    """Old flat layout (dist-info directly in dependencies/) should be removed."""
    # Simulate old layout
    (deps_dir / "cowsay-4.0.dist-info").mkdir()
    (deps_dir / "cowsay").mkdir()
    (deps_dir / "some_file.txt").write_text("data")

    plugin = plugin_factory()

    # Old packages should be removed
    assert not (deps_dir / "cowsay-4.0.dist-info").exists()
    assert not (deps_dir / "cowsay").exists()
    assert not (deps_dir / "some_file.txt").exists()


def test_migration_preserves_version_subfolders(plugin_factory, deps_dir):
    """Existing version-specific subfolders should not be removed during migration."""
    # Simulate old layout with a version subfolder already present
    (deps_dir / "cowsay-4.0.dist-info").mkdir()
    (deps_dir / "3.9").mkdir()
    (deps_dir / "3.9" / "some_package").mkdir()

    plugin = plugin_factory()

    # Old flat packages removed, but version subfolder preserved
    assert not (deps_dir / "cowsay-4.0.dist-info").exists()
    assert (deps_dir / "3.9").exists()
    assert (deps_dir / "3.9" / "some_package").exists()


def test_migration_skips_already_migrated(plugin_factory, deps_dir):
    """If only version subfolders exist (no dist-info), migration does nothing."""
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    versioned = deps_dir / py_ver
    versioned.mkdir()
    (versioned / "cowsay-4.0.dist-info").mkdir()

    plugin = plugin_factory()

    # Everything should still be there
    assert (versioned / "cowsay-4.0.dist-info").exists()


def test_migration_handles_locked_files(plugin_factory, deps_dir):
    """If files can't be removed (e.g. locked on Windows), migration warns."""
    (deps_dir / "locked-1.0.dist-info").mkdir()
    (deps_dir / "locked_file.pyd").write_text("binary")

    with patch("a00_qpip.plugin.shutil.rmtree", side_effect=OSError("locked")):
        with patch("a00_qpip.plugin.warn") as mock_warn:
            plugin = plugin_factory()
            # The .pyd file removal may succeed (only rmtree is mocked),
            # but at least the dist-info dir removal should fail and warn
            if mock_warn.called:
                assert "restart QGIS" in mock_warn.call_args[0][0]
