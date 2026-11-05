"""Safe, Windows-first utilities for analyzing and cleaning VS Code caches."""
from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

try:
    import psutil
except ImportError:  # Analysis still works without psutil; process checks become unavailable.
    psutil = None

LOGGER = logging.getLogger("vscode_optimizer")

# Deliberately conservative: never touch settings, extensions, workspace data, or logs.
CACHE_NAMES = ("Cache", "CachedData", "GPUCache", "Code Cache", "DawnCache")
VSCODE_EXE_NAMES = {"code.exe", "code - insiders.exe", "code-insiders.exe"}
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    memory_mb: float
    memory_metric: str


@dataclass(frozen=True)
class ExtensionInfo:
    name: str
    path: Path
    size_mb: float


@dataclass(frozen=True)
class CleanResult:
    target_count: int
    files_found: int
    bytes_found: int
    files_removed: int
    bytes_removed: int
    errors: tuple[str, ...]
    backup_path: Path | None


class VSCodeOptimizer:
    """Analyze VS Code and safely clean a small allowlist of cache folders."""

    def __init__(self, user_data_dir: Path | str | None = None,
                 extensions_dir: Path | str | None = None,
                 backup_dir: Path | str | None = None):
        self.user_data_dir = Path(user_data_dir).expanduser() if user_data_dir else self._default_user_data_dir()
        self.extensions_dir = Path(extensions_dir).expanduser() if extensions_dir else self._default_extensions_dir()
        self.backup_dir = Path(backup_dir).expanduser() if backup_dir else Path.home() / "Documents" / "VSCodeCacheBackups"

    @staticmethod
    def _default_user_data_dir() -> Path:
        if os.name == "nt":
            appdata = os.environ.get("APPDATA")
            if appdata:
                return Path(appdata) / "Code"
        return Path.home() / ".config" / "Code"

    @staticmethod
    def _default_extensions_dir() -> Path:
        return Path.home() / ".vscode" / "extensions"

    @staticmethod
    def _is_reparse_or_symlink(path: Path) -> bool:
        try:
            info = path.lstat()
        except OSError:
            return True
        return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT)

    @classmethod
    def _safe_regular_files(cls, directory: Path) -> Iterable[Path]:
        """Yield regular files without traversing symlinks/junctions."""
        if cls._is_reparse_or_symlink(directory):
            return
        for current, dirs, names in os.walk(directory, topdown=True, followlinks=False):
            current_path = Path(current)
            dirs[:] = [
                name for name in dirs
                if not cls._is_reparse_or_symlink(current_path / name)
            ]
            for name in names:
                candidate = current_path / name
                try:
                    mode = candidate.lstat().st_mode
                    attrs = getattr(candidate.lstat(), "st_file_attributes", 0)
                    if stat.S_ISREG(mode) and not (attrs & REPARSE_POINT):
                        yield candidate
                except OSError:
                    continue

    @staticmethod
    def _format_mb(value: int | float) -> float:
        return round(value / (1024 * 1024), 2)

    def get_vscode_processes(self) -> list[ProcessInfo]:
        if psutil is None:
            LOGGER.warning("psutil is not installed; process analysis is unavailable.")
            return []
        found: list[ProcessInfo] = []
        for proc in psutil.process_iter(["pid", "name", "exe", "memory_info"]):
            try:
                info = proc.info
                name = (info.get("name") or "").lower()
                exe = Path(info.get("exe") or name).name.lower()
                if name not in VSCODE_EXE_NAMES and exe not in VSCODE_EXE_NAMES:
                    continue
                metric = "RSS"
                amount = info.get("memory_info").rss if info.get("memory_info") else 0
                try:
                    full = proc.memory_full_info()
                    if getattr(full, "uss", 0):
                        amount, metric = full.uss, "USS"
                except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError):
                    pass
                found.append(ProcessInfo(
                    pid=int(info.get("pid") or proc.pid),
                    name=info.get("name") or exe,
                    memory_mb=self._format_mb(amount),
                    memory_metric=metric,
                ))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue
        return sorted(found, key=lambda item: item.pid)

    def analyze_extensions(self, limit: int = 20) -> list[ExtensionInfo]:
        """Report installed extension folder sizes. Does not judge usage or remove anything."""
        if not self.extensions_dir.is_dir() or self._is_reparse_or_symlink(self.extensions_dir):
            return []
        results: list[ExtensionInfo] = []
        try:
            children = list(self.extensions_dir.iterdir())
        except OSError as exc:
            LOGGER.warning("Cannot read extensions directory: %s", exc)
            return []
        for child in children:
            if not child.is_dir() or self._is_reparse_or_symlink(child):
                continue
            size = sum(p.stat().st_size for p in self._safe_regular_files(child))
            results.append(ExtensionInfo(child.name, child, self._format_mb(size)))
        return sorted(results, key=lambda item: item.size_mb, reverse=True)[:max(0, limit)]

    def _check_vscode_running(self) -> bool:
        return bool(self.get_vscode_processes())

    def _cache_targets(self) -> list[Path]:
        root = self.user_data_dir
        if not root.exists() or not root.is_dir() or self._is_reparse_or_symlink(root):
            return []
        resolved_root = root.resolve()
        targets = []
        for name in CACHE_NAMES:
            candidate = root / name
            if not candidate.exists() or not candidate.is_dir() or self._is_reparse_or_symlink(candidate):
                continue
            # Only direct children of the selected user-data directory are eligible.
            if candidate.resolve().parent != resolved_root:
                continue
            targets.append(candidate)
        return targets

    def preview_clean(self) -> dict:
        targets = self._cache_targets()
        files = [file for target in targets for file in self._safe_regular_files(target)]
        total = 0
        for file in files:
            try:
                total += file.stat().st_size
            except OSError:
                pass
        return {
            "targets": targets,
            "files_found": len(files),
            "bytes_found": total,
            "size_mb": self._format_mb(total),
        }

    def _create_verified_backup(self, targets: list[Path]) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        final_path = self.backup_dir / f"vscode-cache-backup-{stamp}.zip"
        fd, temp_name = tempfile.mkstemp(prefix=".vscode-backup-", suffix=".tmp", dir=self.backup_dir)
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            expected = 0
            with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for target in targets:
                    for file in self._safe_regular_files(target):
                        try:
                            relative = file.relative_to(self.user_data_dir)
                            archive.write(file, relative.as_posix())
                            expected += 1
                        except (OSError, ValueError) as exc:
                            raise RuntimeError(f"Backup stopped; could not include {file.name}: {exc}") from exc
            with zipfile.ZipFile(temp_path, "r") as archive:
                bad_member = archive.testzip()
                if bad_member is not None or len(archive.namelist()) != expected:
                    raise RuntimeError("Backup verification failed.")
            if final_path.exists():
                final_path = self.backup_dir / f"vscode-cache-backup-{stamp}-{os.getpid()}.zip"
            temp_path.replace(final_path)
            return final_path
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    @classmethod
    def _remove_tree_contents(cls, target: Path) -> tuple[int, int, list[str]]:
        removed_files = removed_bytes = 0
        errors: list[str] = []
        # Re-check immediately before walking; never follow links/reparse points.
        if cls._is_reparse_or_symlink(target):
            return 0, 0, [f"Skipped unsafe path: {target}"]
        for current, dirs, names in os.walk(target, topdown=True, followlinks=False):
            current_path = Path(current)
            safe_dirs = []
            for name in dirs:
                child = current_path / name
                if cls._is_reparse_or_symlink(child):
                    errors.append(f"Skipped link/junction: {child}")
                else:
                    safe_dirs.append(name)
            dirs[:] = safe_dirs
            for name in names:
                file_path = current_path / name
                if cls._is_reparse_or_symlink(file_path):
                    errors.append(f"Skipped link/reparse point: {file_path}")
                    continue
                try:
                    size = file_path.stat().st_size
                    file_path.unlink()
                    removed_files += 1
                    removed_bytes += size
                except OSError as exc:
                    errors.append(f"Could not remove {file_path}: {exc}")
        # Remove empty directories from deepest to shallowest; keep cache root itself.
        all_dirs = []
        for current, dirs, _ in os.walk(target, topdown=True, followlinks=False):
            current_path = Path(current)
            dirs[:] = [d for d in dirs if not cls._is_reparse_or_symlink(current_path / d)]
            all_dirs.extend(current_path / d for d in dirs)
        for directory in sorted(all_dirs, key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        return removed_files, removed_bytes, errors

    def clean_cache(self, *, confirm: bool = False, backup: bool = True) -> CleanResult:
        """Clean cache folders only. A preview and explicit confirmation are required."""
        if not confirm:
            raise ValueError("Cleaning requires explicit confirmation. Run preview first, then pass confirm=True.")
        if self._check_vscode_running():
            raise RuntimeError("Close all Visual Studio Code windows and try again.")
        preview = self.preview_clean()
        targets = preview["targets"]
        if not targets:
            return CleanResult(0, 0, 0, 0, 0, (), None)
        if not backup:
            raise ValueError("A verified backup is required; backup=False is intentionally disabled.")
        backup_path = self._create_verified_backup(targets)
        removed_files = removed_bytes = 0
        errors: list[str] = []
        for target in targets:
            count, size, target_errors = self._remove_tree_contents(target)
            removed_files += count
            removed_bytes += size
            errors.extend(target_errors)
        return CleanResult(
            len(targets), preview["files_found"], preview["bytes_found"],
            removed_files, removed_bytes, tuple(errors), backup_path
        )
