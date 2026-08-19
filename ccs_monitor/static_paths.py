from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath


class StaticPathError(ValueError):
    pass


class StaticPathResolver:
    """Store managed assets as portable paths and expose resolved runtime paths."""

    def __init__(self, config_path: str | Path, asset_root: str | Path) -> None:
        self.config_path = Path(config_path)
        self.asset_root = Path(asset_root).resolve()
        try:
            common = os.path.commonpath((
                str(self.config_path.parent.resolve()), str(self.asset_root),
            ))
            self.storage_root = Path(common)
        except ValueError:
            self.storage_root = self.config_path.parent.resolve()

    def resolve(self, stored_path: str | Path, *, allow_missing: bool = False) -> Path:
        value = str(stored_path).strip()
        if not value:
            raise StaticPathError("静态资源路径不能为空")
        path = Path(value)
        normalized = value.replace("\\", "/")
        portable_path = PurePosixPath(normalized)
        if ".." in portable_path.parts:
            raise StaticPathError("静态资源相对路径不能越出软件目录")
        filename = portable_path.name
        if not filename:
            raise StaticPathError("静态资源路径必须指向文件")
        foreign_absolute = PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()
        candidates: list[Path] = []
        if path.is_absolute() or foreign_absolute:
            # Never keep using another installation's file, even when that path is
            # still mounted. The managed copy in this installation is authoritative.
            candidates.append(self.asset_root / filename)
            if path.is_absolute():
                candidates.append(path)
        else:
            candidates.extend((
                self.storage_root / path,
                self.config_path.parent / path,
                self.asset_root / path,
                self.asset_root / filename,
            ))
        unique: list[Path] = []
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in unique:
                unique.append(resolved)
        selected = next(
            (item for item in unique if item.is_file() and self._is_managed(item)), None
        )
        if selected is None:
            selected = next((item for item in unique if self._is_managed(item)), unique[0])
            if not allow_missing:
                raise StaticPathError(f"静态资源不存在：{value}")
        if not self._is_managed(selected):
            raise StaticPathError("静态资源必须位于软件数据目录中")
        return selected

    def portable(self, runtime_path: str | Path | None) -> str | None:
        if runtime_path is None or not str(runtime_path).strip():
            return None
        path = Path(runtime_path).resolve()
        if not self._is_managed(path):
            raise StaticPathError("静态资源必须位于软件数据目录中")
        try:
            relative = path.relative_to(self.storage_root)
        except ValueError as exc:
            raise StaticPathError("静态资源无法转换为本地相对路径") from exc
        return relative.as_posix()

    def portable_asset(self, runtime_path: str | Path | None) -> str | None:
        """Return a path relative to the managed asset directory itself."""
        if runtime_path is None or not str(runtime_path).strip():
            return None
        path = Path(runtime_path).resolve()
        try:
            relative = path.relative_to(self.asset_root)
        except ValueError as exc:
            raise StaticPathError("静态资源必须位于软件数据目录中") from exc
        return relative.as_posix()

    def _is_managed(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.asset_root)
            return True
        except ValueError:
            return False
