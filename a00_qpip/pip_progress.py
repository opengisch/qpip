import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import List, Optional
from urllib.parse import urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import (
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)


@dataclass(frozen=True)
class ProgressUpdate:
    package: Optional[str]
    status: str
    current: Optional[int] = None
    total: Optional[int] = None


def requirement_name(requirement: str) -> str:
    """Return a stable display name for a PEP 508 or legacy pip argument."""
    try:
        return Requirement(requirement).name
    except InvalidRequirement:
        value = requirement.split(";", 1)[0].strip()
        value = value.split(" @ ", 1)[0].strip()
        match = re.match(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value)
        return match.group(0) if match else value


def package_from_download(value: str) -> Optional[str]:
    """Extract a distribution name from a pip download URL or filename."""
    filename = PurePosixPath(urlsplit(value).path).name
    if filename.endswith(".metadata"):
        filename = filename[: -len(".metadata")]

    try:
        if filename.endswith(".whl"):
            return str(parse_wheel_filename(filename)[0])
        return str(parse_sdist_filename(filename)[0])
    except (InvalidRequirement, ValueError):
        return None


class PipProgressParser:
    """Convert pip's line-oriented output into package progress updates."""

    _collecting = re.compile(r"^Collecting\s+(.+?)(?:\s+\(from .*)?$")
    _already = re.compile(r"^Requirement already satisfied:\s+([^\s,]+)")
    _download = re.compile(r"^(?:Downloading|Using cached)\s+(\S+)")
    _raw_progress = re.compile(r"^Progress\s+(\d+)\s+of\s+(\d+)$")
    _installing = re.compile(r"^Installing collected packages:\s*(.+)$")

    def __init__(self, requirements=()):
        self.known_names = {}
        for requirement in requirements:
            self._remember(requirement_name(requirement))
        self.active_download = None
        self.installing = []

    def _remember(self, name: str) -> str:
        key = canonicalize_name(name)
        self.known_names.setdefault(key, name)
        return self.known_names[key]

    def parse_line(self, line: str) -> List[ProgressUpdate]:
        line = line.strip()
        if not line:
            return []

        match = self._collecting.match(line)
        if match:
            name = self._remember(requirement_name(match.group(1)))
            return [ProgressUpdate(name, "Resolving")]

        match = self._already.match(line)
        if match:
            name = self._remember(requirement_name(match.group(1)))
            return [ProgressUpdate(name, "Already installed", 1, 1)]

        match = self._download.match(line)
        if match:
            name = package_from_download(match.group(1))
            if name:
                name = self._remember(name)
                self.active_download = name
                status = (
                    "Using cache" if line.startswith("Using cached") else "Downloading"
                )
                return [ProgressUpdate(name, status)]
            return []

        match = self._raw_progress.match(line)
        if match and self.active_download:
            current, total = (int(value) for value in match.groups())
            status = "Downloaded" if total and current >= total else "Downloading"
            return [ProgressUpdate(self.active_download, status, current, total)]

        match = self._installing.match(line)
        if match:
            self.installing = [
                self._remember(name.strip())
                for name in match.group(1).split(",")
                if name.strip()
            ]
            return [ProgressUpdate(name, "Installing") for name in self.installing]

        if line.startswith("Successfully installed"):
            return [ProgressUpdate(name, "Completed", 1, 1) for name in self.installing]

        if line.startswith("ERROR:"):
            return [ProgressUpdate(None, "Error")]

        return []
