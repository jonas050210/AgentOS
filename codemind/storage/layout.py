"""Storage layout: internal storage plus an optional removable USB drive.

Design rules that matter for reliability:

* nothing is hardcoded - the USB mount point is discovered from ``/proc/mounts`` and
  ``/sys/block/*/removable``, or taken from the user's explicit override;
* the internal SSD carries the database and active workspaces because they are latency
  sensitive, while datasets, checkpoints, model files and logs can live on the USB drive;
* when the drive disappears, every write silently falls back to the internal mirror and is
  recorded as *pending*; re-attaching the drive and pressing "Synchronise" copies the queued
  artefacts over. No task state is ever lost because a stick was unplugged.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.utils.files import disk_usage, ensure_dir

log = get_logger("storage.layout")

CATEGORIES = ("datasets", "checkpoints", "models", "logs", "artifacts", "projects")
REMOVABLE_ROOTS = ("/media", "/run/media", "/mnt", "/media/%s" % os.environ.get("USER", "user"))
_MOUNT_ESCAPES = {r"\040": " ", r"\011": "\t", r"\012": "\n", r"\134": "\\"}


@dataclass
class UsbVolume:
    path: Path
    device: str
    label: str
    removable: bool
    total_bytes: int
    free_bytes: int
    marked: bool
    mount_options: str = ""

    @property
    def writable(self) -> bool:
        return "ro" not in self.mount_options.split(",") and os.access(self.path, os.W_OK)

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "device": self.device,
            "label": self.label,
            "removable": self.removable,
            "total_bytes": self.total_bytes,
            "free_bytes": self.free_bytes,
            "marked": self.marked,
            "writable": self.writable,
        }


def _unescape(value: str) -> str:
    for encoded, plain in _MOUNT_ESCAPES.items():
        value = value.replace(encoded, plain)
    return value


def _read_mounts() -> list[tuple[str, Path, str, str]]:
    entries: list[tuple[str, Path, str, str]] = []
    try:
        with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 4:
                    continue
                device, mountpoint, fstype, options = parts[0], _unescape(parts[1]), parts[2], parts[3]
                entries.append((device, Path(mountpoint), fstype, options))
    except OSError as exc:  # pragma: no cover - /proc always present on Linux
        log.warning("could not read /proc/mounts", cm={"error": str(exc)})
    return entries


def _is_removable_device(mount_path: Path) -> bool:
    if mount_path == Path("/"):
        return False
    for root in REMOVABLE_ROOTS:
        try:
            if mount_path.is_relative_to(root):
                return True
        except (ValueError, AttributeError):
            if str(mount_path).startswith(root):
                return True
    # Fall back to the sysfs flag for anything mounted from a removable block device.
    try:
        device = os.path.realpath(str(mount_path))
        parent = None
        for entry in _read_mounts():
            if entry[1] == mount_path:
                parent = os.path.basename(entry[0]).rstrip("0123456789")
                break
        if parent:
            flag = Path(f"/sys/block/{parent}/removable")
            if flag.exists() and flag.read_text().strip() == "1":
                return True
    except OSError:
        pass
    return False


def detect_usb_volumes(marker: str = ".codemind") -> list[UsbVolume]:
    """Discover candidate removable volumes, marked ones first."""
    volumes: list[UsbVolume] = []
    seen: set[str] = set()
    for device, mountpoint, fstype, options in _read_mounts():
        if fstype in ("proc", "sysfs", "cgroup", "cgroup2", "tmpfs", "devpts", "overlay", "squashfs", "efivarfs", "ramfs", "debugfs", "tracefs", "fuse.gvfsd-fuse", "autofs"):
            continue
        if not device.startswith("/dev/"):
            continue
        if not _is_removable_device(mountpoint):
            continue
        key = str(mountpoint)
        if key in seen:
            continue
        usage = disk_usage(mountpoint)
        if usage is None:
            continue
        total, _used, free = usage
        marked = (mountpoint / marker).exists() or (mountpoint / "codemind" / marker).exists()
        volumes.append(
            UsbVolume(
                path=mountpoint,
                device=device,
                label=mountpoint.name or device,
                removable=True,
                total_bytes=total,
                free_bytes=free,
                marked=marked,
                mount_options=options,
            )
        )
        seen.add(key)
    volumes.sort(key=lambda v: (not v.marked, str(v.path)))
    return volumes


def default_data_root() -> Path:
    env = os.environ.get("CODEMIND_DATA_DIR") or os.environ.get("CODEMIND_HOME")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "codemind"


@dataclass
class CategoryPath:
    category: str
    primary: Path
    fallback: Path
    on_usb: bool

    @property
    def active(self) -> Path:
        return self.primary if self.on_usb else self.primary

    def degraded(self) -> bool:
        return self.on_usb and not self.primary.exists()


@dataclass
class StorageLayout:
    """Resolves where each kind of artefact lives, and tracks USB availability."""

    data_root: Path
    usb_override: str = ""
    marker: str = ".codemind"
    usb_categories: tuple[str, ...] = ()
    projects_root: str = ""
    workspace_root: str = ""
    _volumes: list[UsbVolume] = field(default_factory=list)
    _pending: list[dict[str, object]] = field(default_factory=list)
    _last_scan: float = 0.0
    _usb_active: Path | None = None

    # -- construction -----------------------------------------------------
    @classmethod
    def create(
        cls,
        data_root: str | os.PathLike[str],
        *,
        usb_override: str = "",
        marker: str = ".codemind",
        usb_categories: Iterable[str] = (),
        projects_root: str = "",
        workspace_root: str = "",
    ) -> "StorageLayout":
        layout = cls(
            data_root=Path(data_root).expanduser(),
            usb_override=usb_override,
            marker=marker,
            usb_categories=tuple(usb_categories),
            projects_root=projects_root,
            workspace_root=workspace_root,
        )
        layout.ensure_internal()
        layout.scan(force=True)
        return layout

    # -- internal paths ---------------------------------------------------
    def ensure_internal(self) -> None:
        ensure_dir(self.data_root)
        for name in ("logs", "workspaces", "projects", "datasets", "checkpoints", "models", "artifacts", "cache", "pending"):
            ensure_dir(self.data_root / name)

    @property
    def db_path(self) -> Path:
        return self.data_root / "codemind.db"

    @property
    def settings_path(self) -> Path:
        return self.data_root / "settings.json"

    def internal(self, category: str) -> Path:
        return self.data_root / category

    # -- usb --------------------------------------------------------------
    def scan(self, force: bool = False) -> list[UsbVolume]:
        if not force and time.time() - self._last_scan < 5.0:
            return self._volumes
        self._last_scan = time.time()
        volumes: list[UsbVolume] = []
        if self.usb_override:
            candidate = Path(self.usb_override).expanduser()
            if candidate.exists():
                usage = disk_usage(candidate)
                total, _used, free = usage if usage else (0, 0, 0)
                volumes.append(
                    UsbVolume(
                        path=candidate,
                        device="(configured)",
                        label=candidate.name or "configured",
                        removable=False,
                        total_bytes=total,
                        free_bytes=free,
                        marked=(candidate / self.marker).exists(),
                    )
                )
        for volume in detect_usb_volumes(self.marker):
            if volume.path not in {v.path for v in volumes}:
                volumes.append(volume)
        previous = self._usb_active
        usable = [v for v in volumes if v.marked and v.writable] or [v for v in volumes if v.writable]
        self._usb_active = usable[0].path if usable else None
        self._volumes = volumes
        if previous != self._usb_active:
            if self._usb_active is None:
                log.info("removable storage unavailable; using internal storage", cm={"previous": str(previous)})
            else:
                log.info("removable storage active", cm={"path": str(self._usb_active)})
            BUS.publish(Topics.STORAGE_CHANGED, usb=str(self._usb_active) if self._usb_active else None, volumes=[v.as_dict() for v in volumes])
        return volumes

    @property
    def volumes(self) -> list[UsbVolume]:
        self.scan()
        return self._volumes

    @property
    def usb_active(self) -> Path | None:
        self.scan()
        return self._usb_active

    def usb_root(self, category: str) -> Path | None:
        """Directory for ``category`` on the USB drive, or None when it is not attached."""
        active = self.usb_active
        if active is None or category not in self.usb_categories:
            return None
        marker_dir = active / "codemind"
        base = marker_dir if (marker_dir / self.marker).exists() or (active / self.marker).exists() else marker_dir
        return base / category

    def initialize_usb(self, path: str | os.PathLike[str] | None = None) -> Path:
        target = Path(path).expanduser() if path else self.usb_active
        if target is None:
            raise RuntimeError("no removable volume detected")
        ensure_dir(target / "codemind")
        for name in ("datasets", "checkpoints", "models", "logs", "artifacts", "projects"):
            ensure_dir(target / "codemind" / name)
        (target / self.marker).write_text(
            "CodeMind storage marker\ncreated: %s\n" % time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8"
        )
        self.scan(force=True)
        log.info("initialised removable storage", cm={"path": str(target)})
        return target

    # -- resolution -------------------------------------------------------
    def resolve(self, category: str) -> Path:
        """Return the directory for ``category``, falling back when the USB drive is gone."""
        internal = self.internal(category)
        root = self.usb_root(category)
        if root is None:
            ensure_dir(internal)
            return internal
        try:
            ensure_dir(root)
            return root
        except OSError as exc:
            log.warning("removable storage not writable, using internal path", cm={"path": str(root), "error": str(exc)})
            ensure_dir(internal)
            return internal

    def category_info(self, category: str) -> dict[str, object]:
        path = self.resolve(category)
        root = self.usb_root(category)
        usage = disk_usage(path)
        return {
            "category": category,
            "path": str(path),
            "on_usb": bool(root and str(path).startswith(str(root.parent.parent))),
            "free_bytes": usage[2] if usage else 0,
            "total_bytes": usage[0] if usage else 0,
            "usb_available": self.usb_active is not None,
        }

    def paths(self, category: str, *parts: str) -> Path:
        target = self.resolve(category)
        for part in parts:
            target = target / part
        return target

    def projects_dir(self) -> Path:
        if self.projects_root:
            path = Path(self.projects_root).expanduser()
        else:
            path = self.internal("projects")
        ensure_dir(path)
        return path

    def workspaces_dir(self) -> Path:
        if self.workspace_root:
            path = Path(self.workspace_root).expanduser()
        else:
            path = self.internal("workspaces")
        ensure_dir(path)
        return path

    # -- degradation handling --------------------------------------------
    def record_pending(self, category: str, relative: str, note: str = "") -> None:
        """Remember an artefact written to the fallback path while the USB drive was absent."""
        self._pending.append({"category": category, "relative": relative, "note": note, "ts": time.time()})
        self._flush_pending()

    def _pending_file(self) -> Path:
        return self.data_root / "pending" / "usb_sync.json"

    def _flush_pending(self) -> None:
        from codemind.utils.files import atomic_write_json, read_json

        existing = read_json(self._pending_file(), default=[]) or []
        merged: dict[str, dict[str, object]] = {}
        for item in list(existing) + self._pending:
            if isinstance(item, dict) and item.get("category") and item.get("relative"):
                merged[f"{item['category']}/{item['relative']}"] = item
        self._pending = []
        atomic_write_json(self._pending_file(), list(merged.values()))

    def pending_items(self) -> list[dict[str, object]]:
        from codemind.utils.files import read_json

        return list(read_json(self._pending_file(), default=[]) or [])

    def sync_pending(self) -> dict[str, int]:
        """Copy artefacts queued during USB absence onto the drive. Returns counts."""
        result = {"copied": 0, "failed": 0, "skipped": 0}
        pending = self.pending_items()
        if not pending:
            return result
        remaining: list[dict[str, object]] = []
        for item in pending:
            category = str(item.get("category"))
            relative = str(item.get("relative"))
            source = self.internal(category) / relative
            target_root = self.usb_root(category)
            if target_root is None:
                remaining.append(item)
                result["skipped"] += 1
                continue
            target = target_root / relative
            try:
                if source.is_dir():
                    ensure_dir(target)
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    ensure_dir(target.parent)
                    shutil.copy2(source, target)
                result["copied"] += 1
            except (OSError, shutil.Error):
                result["failed"] += 1
                remaining.append(item)
        from codemind.utils.files import atomic_write_json

        atomic_write_json(self._pending_file(), remaining)
        if result["copied"]:
            log.info("synchronised queued artefacts to removable storage", cm=result)
        return result

    # -- reporting --------------------------------------------------------
    def summary(self) -> dict[str, object]:
        volumes = self.volumes
        active = self.usb_active
        usage = disk_usage(self.data_root)
        return {
            "data_root": str(self.data_root),
            "db_path": str(self.db_path),
            "usb_active": str(active) if active else None,
            "usb_volumes": [v.as_dict() for v in volumes],
            "usb_categories": list(self.usb_categories),
            "pending_sync": len(self.pending_items()),
            "internal_free_bytes": usage[2] if usage else 0,
            "internal_total_bytes": usage[0] if usage else 0,
            "categories": {name: self.category_info(name) for name in CATEGORIES},
        }
