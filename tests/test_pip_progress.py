from a00_qpip.pip_progress import (
    PipProgressParser,
    package_from_download,
    requirement_name,
)


def test_requirement_name_handles_pep508_and_legacy_values():
    assert requirement_name("harmonica>=0.7,<0.8") == "harmonica"
    assert requirement_name("demo[extra] @ https://example.com/demo.whl") == "demo"


def test_package_from_wheel_and_sdist_downloads():
    assert package_from_download("numpy-2.2.6-cp311-cp311-win_amd64.whl") == "numpy"
    assert package_from_download("https://example.com/scipy-1.17.1.tar.gz") == "scipy"


def test_parser_tracks_raw_download_progress():
    parser = PipProgressParser(["numpy==2.2.6"])

    assert parser.parse_line("Collecting numpy==2.2.6")[0].status == "Resolving"
    update = parser.parse_line(
        "Downloading numpy-2.2.6-cp311-cp311-win_amd64.whl (12.9 MB)"
    )[0]
    assert update.package == "numpy"
    assert update.status == "Downloading"

    update = parser.parse_line("Progress 262144 of 12907455")[0]
    assert (update.current, update.total) == (262144, 12907455)
    assert update.status == "Downloading"

    update = parser.parse_line("Progress 12907455 of 12907455")[0]
    assert update.status == "Downloaded"


def test_parser_discovers_transitive_packages_and_marks_install_complete():
    parser = PipProgressParser(["harmonica>=0.7"])

    update = parser.parse_line("Collecting xrft>=1.0 (from harmonica>=0.7)")[0]
    assert update.package == "xrft"
    assert update.status == "Resolving"

    updates = parser.parse_line("Installing collected packages: xrft, harmonica")
    assert [(update.package, update.status) for update in updates] == [
        ("xrft", "Installing"),
        ("harmonica", "Installing"),
    ]

    updates = parser.parse_line("Successfully installed harmonica-0.7.0 xrft-1.0.1")
    assert all(update.status == "Completed" for update in updates)


def test_parser_marks_satisfied_requirement_complete():
    parser = PipProgressParser(["packaging>=23"])
    update = parser.parse_line(
        "Requirement already satisfied: packaging>=23 in C:/qgis/python (25.0)"
    )[0]
    assert update.package == "packaging"
    assert update.status == "Already installed"
    assert (update.current, update.total) == (1, 1)
