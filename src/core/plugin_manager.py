# core/plugin_manager.py
"""插件管理器：扫描 plugins/ 目录，按依赖顺序加载/卸载插件。

设计要点：
- 清单驱动：每个插件包根目录放 plugin.json，声明 id / 入口 / 依赖；
- 依赖排序：按 deps 做拓扑排序，缺依赖或成环的插件跳过并记错；
- 服务校验：requires 里的内核服务未注册时拒绝加载，避免运行时才炸；
- 故障隔离：单个插件激活抛异常不影响其余插件与主程序；
- 可启停：启用状态来自 config["plugins"]["disabled"]，支持运行时卸载。

新增一个功能只需：
    1) 在 plugins/ 下建一个包，写好 plugin.json 与入口类；
    2) 如需禁用/启用，改配置里的 plugins.disabled 列表。
无需改动内核与外壳。
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.sdk.context import PluginContext
from core.sdk.plugin_base import PluginManifest

if TYPE_CHECKING:
    from core.kernel import Kernel

logger = logging.getLogger(__name__)


class PluginState:
    UNLOADED = "unloaded"
    DISABLED = "disabled"
    ACTIVE = "active"
    FAILED = "failed"


@dataclass
class PluginRecord:
    manifest: PluginManifest
    instance: Any | None = None
    state: str = PluginState.UNLOADED
    error: str | None = None


class PluginError(RuntimeError):
    """插件依赖或清单层面的错误。"""


class PluginManager:
    def __init__(self, kernel: "Kernel", plugins_root: Path | None = None,
                 package_prefix: str = "plugins") -> None:
        self.kernel = kernel
        self.plugins_root = Path(plugins_root) if plugins_root else Path(kernel.root_dir) / "plugins"
        self.package_prefix = package_prefix
        self._records: dict[str, PluginRecord] = {}

    # ---------------- 发现 ----------------
    def discover(self) -> list[PluginManifest]:
        manifests: list[PluginManifest] = []
        if not self.plugins_root.is_dir():
            logger.warning("插件目录不存在: %s", self.plugins_root)
            return manifests
        for manifest_path in sorted(self.plugins_root.glob("*/plugin.json")):
            try:
                manifests.append(PluginManifest.from_file(manifest_path))
            except Exception:
                logger.exception("插件清单解析失败: %s", manifest_path)
        return manifests

    # ---------------- 依赖排序 ----------------
    def _sort_by_deps(self, manifests: list[PluginManifest]) -> list[PluginManifest]:
        by_id = {m.id: m for m in manifests}
        ordered: list[PluginManifest] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(manifest: PluginManifest) -> None:
            if manifest.id in visited:
                return
            if manifest.id in visiting:
                raise PluginError(f"插件依赖存在环: {manifest.id}")
            visiting.add(manifest.id)
            for dep in manifest.deps:
                if dep not in by_id:
                    raise PluginError(f"插件 [{manifest.id}] 依赖的插件不存在: {dep}")
                visit(by_id[dep])
            visiting.discard(manifest.id)
            visited.add(manifest.id)
            ordered.append(manifest)

        for manifest in manifests:
            visit(manifest)
        return ordered

    # ---------------- 加载 ----------------
    def load_all(self) -> list[PluginRecord]:
        manifests = self.discover()
        try:
            ordered = self._sort_by_deps(manifests)
        except PluginError as exc:
            logger.error("插件依赖校验失败：%s", exc)
            return []
        return [self.load(manifest) for manifest in ordered]

    def _plugin_dir(self, manifest: PluginManifest) -> Path:
        """插件包目录：plugins/<id>/（冻结环境下同样可用）。"""
        return Path(self.plugins_root) / manifest.id

    def _register_plugin_i18n(self, manifest: PluginManifest) -> None:
        """注册插件私有语言包（plugins/<id>/i18n/），缺失则静默跳过。"""
        i18n = self.kernel.services.get("i18n")
        if i18n is None:
            return
        directory = self._plugin_dir(manifest) / "i18n"
        if directory.is_dir():
            i18n.register_namespace(manifest.id, directory)

    def _entry_path(self, manifest: PluginManifest) -> str:
        """入口类的完整导入路径，例如 plugins.navigator.plugin。"""
        return f"{self.package_prefix}.{manifest.id}.{manifest.entry_module}"

    def load(self, manifest: PluginManifest, force: bool = False) -> PluginRecord:
        """加载单个插件。force=True 时忽略启用状态（用于将来从界面手动启用）。"""
        record = self._records.setdefault(manifest.id, PluginRecord(manifest=manifest))

        if not force and not self._is_enabled(manifest):
            record.state = PluginState.DISABLED
            record.instance = None
            logger.info("插件未启用，已跳过: %s", manifest.id)
            return record

        # 插件词条要在 activate() 之前就绪，否则菜单 label 无法翻译
        self._register_plugin_i18n(manifest)

        missing = [name for name in manifest.requires if name not in self.kernel.services]
        if missing:
            record.state = PluginState.FAILED
            record.error = f"缺少内核服务: {', '.join(missing)}"
            logger.error("插件 [%s] 加载失败：%s", manifest.id, record.error)
            return record

        try:
            module = importlib.import_module(self._entry_path(manifest))
            plugin_cls = getattr(module, manifest.entry_class)
            instance = plugin_cls()
            instance.activate(PluginContext(self.kernel, manifest))
        except Exception as exc:
            record.state = PluginState.FAILED
            record.error = str(exc)
            record.instance = None
            logger.exception("插件 [%s] 激活失败", manifest.id)
            return record

        record.instance = instance
        record.state = PluginState.ACTIVE
        record.error = None
        logger.info("插件已加载: %s v%s", manifest.id, manifest.version)
        return record

    # ---------------- 卸载 ----------------
    def unload(self, plugin_id: str) -> None:
        record = self._records.get(plugin_id)
        if record is None or record.state != PluginState.ACTIVE:
            return
        # 先关窗口再 deactivate：窗口由 WindowManager 持有，
        # 若反过来，插件在 deactivate 里可能访问已被销毁的 C++ 对象
        try:
            windows = self.kernel.services.get("window_manager")
            if windows is not None:
                windows.close_owner(plugin_id)
        except Exception:
            logger.exception("插件 [%s] 的窗口清理失败", plugin_id)
        try:
            record.instance.deactivate()
        except Exception:
            logger.exception("插件 [%s] 停用异常", plugin_id)
        i18n = self.kernel.services.get("i18n")
        if i18n is not None:
            i18n.unregister_namespace(plugin_id)
        hotkeys = self.kernel.services.get("hotkeys")
        if hotkeys is not None:
            hotkeys.unregister_owner(plugin_id)
        try:
            self.kernel.ui.remove_plugin(plugin_id)
        except Exception:
            logger.exception("插件 [%s] 的 UI 贡献清理失败", plugin_id)
        record.instance = None
        record.state = PluginState.UNLOADED
        record.error = None

    def unload_all(self) -> None:
        for plugin_id in reversed(list(self._records)):
            self.unload(plugin_id)

    # ---------------- 查询 ----------------
    def _is_enabled(self, manifest: PluginManifest) -> bool:
        if not manifest.enabled_by_default:
            return False
        config = self.kernel.services.get("config")
        if config is None:
            return True
        disabled = (config.get("plugins", {}) or {}).get("disabled", [])
        return manifest.id not in disabled

    def get(self, plugin_id: str) -> PluginRecord | None:
        return self._records.get(plugin_id)

    def all(self) -> list[PluginRecord]:
        return list(self._records.values())

    @property
    def records(self) -> dict[str, PluginRecord]:
        return self._records
