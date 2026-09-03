from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from .runtime_paths import application_root
from pathlib import Path
from typing import Any, Callable

from PySide6.QtGui import QImageReader
from PySide6.QtSvg import QSvgRenderer

from .models import (
    DEFAULT_DEVICE_STATUS_CARDS,
    DEVICE_STATUS_CARD_CATALOG,
    DeviceTypeTemplate,
    MapMarkerShape,
)
from .static_paths import StaticPathError, StaticPathResolver


DEVICE_TYPE_SCHEMA_VERSION = 1
TYPE_ID_PATTERN = re.compile(r"^[A-Z0-9_-]{2,16}$")
SUPPORTED_ICON_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg"}
MAX_ICON_BYTES = 5 * 1024 * 1024
DEFAULT_DEVICE_TYPES_PATH = application_root() / "config" / "device_types.json"
DEFAULT_DEVICE_TYPE_ASSET_DIR = application_root() / "data" / "device_type_assets"


class DeviceTypeConfigError(RuntimeError):
    pass


def default_device_type_templates() -> list[DeviceTypeTemplate]:
    return [
        DeviceTypeTemplate("UGV", "无人地面车", map_marker_shape=MapMarkerShape.CUBE),
        DeviceTypeTemplate("UAV", "无人机", map_marker_shape=MapMarkerShape.ARROW),
        DeviceTypeTemplate("AMR", "自主移动机器人", map_marker_shape=MapMarkerShape.CUBE),
        DeviceTypeTemplate("USV", "无人水面艇", map_marker_shape=MapMarkerShape.ARROW),
    ]


class DeviceTypeTemplateRepository:
    def __init__(
        self,
        path: str | Path = DEFAULT_DEVICE_TYPES_PATH,
        asset_dir: str | Path = DEFAULT_DEVICE_TYPE_ASSET_DIR,
        referenced_type_ids: Callable[[], set[str]] | None = None,
    ) -> None:
        self.path = Path(path)
        self.asset_dir = Path(asset_dir)
        self.path_resolver = StaticPathResolver(self.path, self.asset_dir)
        self.referenced_type_ids = referenced_type_ids or (lambda: set())
        self.read_only = False
        self.error_message: str | None = None
        self._templates: list[DeviceTypeTemplate] = []

    def load(self) -> list[DeviceTypeTemplate]:
        self.read_only = False
        self.error_message = None
        if not self.path.exists():
            self._templates = default_device_type_templates()
            self._write(self._templates)
            return list(self._templates)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self._templates = self._parse(payload)
            stored_paths = [item.get("icon_path") for item in payload["device_types"]]
            portable_paths = [self.path_resolver.portable(item.icon_path) for item in self._templates]
            if stored_paths != portable_paths:
                try:
                    self._write(self._templates)
                except (DeviceTypeConfigError, StaticPathError) as exc:
                    self.read_only = True
                    self.error_message = f"设备类型资源路径迁移失败，配置已切换为只读：{exc}"
        except (OSError, ValueError, TypeError, json.JSONDecodeError, StaticPathError) as exc:
            self.read_only = True
            self.error_message = f"设备类型配置读取失败：{exc}"
            self._templates = []
        return list(self._templates)

    def all(self) -> list[DeviceTypeTemplate]:
        return list(self._templates)

    def get(self, type_id: str) -> DeviceTypeTemplate | None:
        folded = type_id.strip().casefold()
        return next((item for item in self._templates if item.type_id.casefold() == folded), None)

    def create(self, template: DeviceTypeTemplate, icon_source: str | Path | None = None) -> DeviceTypeTemplate:
        self._ensure_writable()
        normalized = self._validate(template)
        if self.get(normalized.type_id):
            raise DeviceTypeConfigError(f"设备类型 {normalized.type_id} 已存在")
        if icon_source is not None:
            normalized = self._with_icon(normalized, icon_source)
        self._write([*self._templates, normalized])
        self._templates.append(normalized)
        return normalized

    def update(self, template: DeviceTypeTemplate, icon_source: str | Path | None = None) -> DeviceTypeTemplate:
        self._ensure_writable()
        normalized = self._validate(template)
        previous = self.get(normalized.type_id)
        if previous is None:
            raise DeviceTypeConfigError(f"设备类型 {normalized.type_id} 不存在")
        if icon_source is not None:
            normalized = self._with_icon(normalized, icon_source)
        updated = [normalized if item.type_id == normalized.type_id else item for item in self._templates]
        self._write(updated)
        self._templates = updated
        self._trash_if_unreferenced(previous.icon_path, except_path=normalized.icon_path)
        return normalized

    def delete(self, type_id: str) -> None:
        self._ensure_writable()
        template = self.get(type_id)
        if template is None:
            raise DeviceTypeConfigError(f"设备类型 {type_id} 不存在")
        if len(self._templates) <= 1:
            raise DeviceTypeConfigError("至少需要保留一个设备类型模板")
        if template.type_id.casefold() in {item.casefold() for item in self.referenced_type_ids()}:
            raise DeviceTypeConfigError("该类型仍被设备引用，不能删除")
        updated = [item for item in self._templates if item.type_id != template.type_id]
        self._write(updated)
        self._templates = updated
        self._trash_if_unreferenced(template.icon_path)

    def _parse(self, payload: Any) -> list[DeviceTypeTemplate]:
        if not isinstance(payload, dict) or payload.get("schema_version") != DEVICE_TYPE_SCHEMA_VERSION:
            raise ValueError("不支持的设备类型配置 schema")
        raw = payload.get("device_types")
        if not isinstance(raw, list) or not raw:
            raise ValueError("device_types 必须是非空数组")
        result: list[DeviceTypeTemplate] = []
        seen: set[str] = set()
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"device_types[{index}] 必须是对象")
            try:
                icon_path = item.get("icon_path")
                if icon_path:
                    icon_path = str(self.path_resolver.resolve(icon_path, allow_missing=True))
                template = self._validate(DeviceTypeTemplate(
                    type_id=item["type_id"],
                    display_name=item["display_name"],
                    icon_path=icon_path,
                    map_marker_shape=MapMarkerShape(item.get("map_marker_shape", "sphere")),
                    default_status_card_ids=tuple(
                        card for card in item.get("default_status_cards", DEFAULT_DEVICE_STATUS_CARDS)
                        if card not in {"octomap_mapping", "occupancy_grid_mapping"}
                    ),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"device_types[{index}] 字段无效：{exc}") from exc
            if template.type_id.casefold() in seen:
                raise ValueError(f"设备类型 ID 重复：{template.type_id}")
            seen.add(template.type_id.casefold())
            result.append(template)
        return result

    @staticmethod
    def _validate(template: DeviceTypeTemplate) -> DeviceTypeTemplate:
        type_id = str(template.type_id).strip().upper()
        display_name = str(template.display_name).strip()
        if not TYPE_ID_PATTERN.fullmatch(type_id):
            raise ValueError("类型 ID 需为 2–16 位大写字母、数字、下划线或连字符")
        if not display_name:
            raise ValueError("类型显示名称不能为空")
        cards: list[str] = []
        for card_id in template.default_status_card_ids:
            if card_id not in DEVICE_STATUS_CARD_CATALOG:
                raise ValueError(f"未知的数据状态卡片：{card_id}")
            if card_id in cards:
                raise ValueError(f"数据状态卡片重复：{card_id}")
            cards.append(card_id)
        return DeviceTypeTemplate(type_id, display_name, template.icon_path, MapMarkerShape(template.map_marker_shape), tuple(cards))

    def _with_icon(self, template: DeviceTypeTemplate, source: str | Path) -> DeviceTypeTemplate:
        source_path = Path(source)
        if not source_path.is_file() or source_path.suffix.lower() not in SUPPORTED_ICON_SUFFIXES:
            raise DeviceTypeConfigError("图标仅支持 PNG、JPEG 或 SVG 文件")
        if source_path.stat().st_size > MAX_ICON_BYTES:
            raise DeviceTypeConfigError("图标文件不能超过 5 MiB")
        valid = QSvgRenderer(str(source_path)).isValid() if source_path.suffix.lower() == ".svg" else QImageReader(str(source_path)).canRead()
        if not valid:
            raise DeviceTypeConfigError("图标文件损坏或无法解码")
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        destination = self.asset_dir / f"{template.type_id.lower()}_{uuid.uuid4().hex[:10]}{source_path.suffix.lower()}"
        try:
            shutil.copy2(source_path, destination)
        except OSError as exc:
            raise DeviceTypeConfigError(f"复制图标失败：{exc}") from exc
        return DeviceTypeTemplate(
            template.type_id, template.display_name, str(destination.resolve()),
            template.map_marker_shape, template.default_status_card_ids,
        )

    def _trash_if_unreferenced(self, icon_path: str | None, except_path: str | None = None) -> None:
        if not icon_path or icon_path == except_path or any(item.icon_path == icon_path for item in self._templates):
            return
        path = Path(icon_path)
        if not path.exists() or path.parent.resolve() != self.asset_dir.resolve():
            return
        trash = self.asset_dir / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(path, trash / f"{path.stem}_{uuid.uuid4().hex[:8]}{path.suffix}")
        except OSError:
            pass

    def _ensure_writable(self) -> None:
        if self.read_only:
            raise DeviceTypeConfigError(self.error_message or "设备类型配置当前为只读状态")

    def _write(self, templates: list[DeviceTypeTemplate]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": DEVICE_TYPE_SCHEMA_VERSION, "device_types": [
            {"type_id": item.type_id, "display_name": item.display_name,
             "icon_path": self.path_resolver.portable(item.icon_path),
             "map_marker_shape": item.map_marker_shape.value, "default_status_cards": list(item.default_status_card_ids)}
            for item in templates
        ]}
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp", delete=False) as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = Path(handle.name)
            os.replace(temporary_path, self.path)
        except OSError as exc:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)
            raise DeviceTypeConfigError(f"设备类型配置写入失败：{exc}") from exc
