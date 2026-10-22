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
import importlib.util
import logging
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.sdk.context import PluginContext
from core.sdk.plugin_base import PluginManifest
from core.services.config_manager import BASE_DIR

if TYPE_CHECKING:
    from core.kernel import Kernel

logger = logging.getLogger(__name__)

#: 用户插件目录：第三方/后装插件放在这里（内置插件在 src/plugins 或包内 plugins/）
USER_PLUGINS_DIR = BASE_DIR / "plugins_user"


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
        """扫描内置目录与用户插件目录；同 id 时用户目录的版本覆盖内置版。"""
        manifests: dict[str, PluginManifest] = {}
        for root in (self.plugins_root, USER_PLUGINS_DIR):
            if not root.is_dir():
                if root is self.plugins_root:
                    logger.warning("插件目录不存在: %s", root)
                continue
            for manifest_path in sorted(root.glob("*/plugin.json")):
                try:
                    manifest = PluginManifest.from_file(manifest_path)
                    # 记录真实来源目录：i18n 注册与模块加载都按它定位
                    manifest._dir = manifest_path.parent          # noqa: SLF001
                    manifest._user_provided = root != self.plugins_root  # noqa: SLF001
                    manifests[manifest.id] = manifest
                except Exception:
                    logger.exception("插件清单解析失败: %s", manifest_path)
        return list(manifests.values())

    def rescan(self) -> None:
        """重新发现插件（安装新插件后调用）；新发现的插件登记为未加载状态。"""
        for manifest in self.discover():
            if manifest.id not in self._records:
                self._records[manifest.id] = PluginRecord(manifest=manifest)

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
        """插件包目录（优先取清单发现时记录的真实目录，兼容用户插件）。"""
        return getattr(manifest, "_dir", None) or Path(self.plugins_root) / manifest.id

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

    def _import_entry_module(self, manifest: PluginManifest):
        """导入插件入口模块。

        内置插件走包路径 import；用户插件目录不在包里，
        按文件位置加载（模块名加 user_plugins 前缀避免撞名）。
        """
        if not getattr(manifest, "_user_provided", False):
            return importlib.import_module(self._entry_path(manifest))
        module_name = f"user_plugins.{manifest.id}.{manifest.entry_module}"
        if module_name in sys.modules:
            return sys.modules[module_name]
        entry_file = self._plugin_dir(manifest) / f"{manifest.entry_module}.py"
        spec = importlib.util.spec_from_file_location(module_name, entry_file)
        if spec is None or spec.loader is None:
            raise PluginError(f"插件入口不可加载: {entry_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

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
            module = self._import_entry_module(manifest)
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
        config = self.kernel.services.get("config")
        plugins_cfg = (config.get("plugins", {}) or {}) if config is not None else {}
        disabled = plugins_cfg.get("disabled", [])
        if manifest.id in disabled:
            return False
        # 用户安装的插件与声明默认关闭的插件：必须显式勾选过才启用
        if getattr(manifest, "_user_provided", False) or not manifest.enabled_by_default:
            return manifest.id in plugins_cfg.get("enabled", [])
        return True

    def is_enabled(self, plugin_id: str) -> bool:
        record = self._records.get(plugin_id)
        if record is not None:
            return record.state == PluginState.ACTIVE
        return False

    # ---------------- 运行时启停 / 排序 / 安装 ----------------
    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """勾选/取消勾选插件：立即加载或卸载，并广播 plugins.changed。

        勾选 = 启用 + 上侧边栏 + 上桌宠右键菜单（三者为同一开关）。
        """
        config = self.kernel.services.get("config")
        if config is not None:
            plugins_cfg = dict(config.get("plugins", {}) or {})
            disabled = list(plugins_cfg.get("disabled", []))
            enabled_list = list(plugins_cfg.get("enabled", []))
            if enabled:
                if plugin_id in disabled:
                    disabled.remove(plugin_id)
                if plugin_id not in enabled_list:
                    enabled_list.append(plugin_id)
            else:
                if plugin_id not in disabled:
                    disabled.append(plugin_id)
                if plugin_id in enabled_list:
                    enabled_list.remove(plugin_id)
            plugins_cfg["disabled"] = disabled
            plugins_cfg["enabled"] = enabled_list
            config.set("plugins", plugins_cfg)

        record = self._records.get(plugin_id)
        if enabled:
            if record is None or record.state != PluginState.ACTIVE:
                manifest = record.manifest if record is not None else self._manifest_of(plugin_id)
                if manifest is not None:
                    self.load(manifest, force=True)
        else:
            self.unload(plugin_id)
            if record is not None:
                record.state = PluginState.DISABLED
        self.kernel.events.emit("plugins.changed", plugin_id)

    def set_order(self, ordered_ids: list[str]) -> None:
        """保存侧边栏顺序并广播 plugins.changed。"""
        config = self.kernel.services.get("config")
        if config is None:
            return
        plugins_cfg = dict(config.get("plugins", {}) or {})
        plugins_cfg["order"] = list(ordered_ids)
        config.set("plugins", plugins_cfg)
        self.kernel.events.emit("plugins.changed", None)

    def install(self, source: str | Path) -> PluginManifest:
        """从文件夹或 zip 安装插件到用户插件目录。

        安装后默认不启用：出现在管理面板，用户在面板里勾选才开始工作。
        """
        source = Path(source)
        USER_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            if source.is_dir():
                src_dir = source
            elif source.suffix.lower() == ".zip":
                with zipfile.ZipFile(source) as zf:
                    zf.extractall(tmp)
                # 允许 zip 里套一层同名目录
                candidates = [p for p in Path(tmp).rglob("plugin.json")]
                if not candidates:
                    raise PluginError("压缩包内未找到 plugin.json")
                src_dir = candidates[0].parent
            else:
                raise PluginError(f"不支持的安装来源: {source}")

            manifest = PluginManifest.from_file(src_dir / "plugin.json")
            dest = USER_PLUGINS_DIR / manifest.id
            if dest.exists():
                shutil.rmtree(dest)   # 同 id 覆盖安装（限用户插件目录内）
            shutil.copytree(src_dir, dest)

        manifest._dir = dest                      # noqa: SLF001
        manifest._user_provided = True            # noqa: SLF001
        record = self._records.get(manifest.id)
        if record is None or record.state != PluginState.ACTIVE:
            self._records[manifest.id] = PluginRecord(
                manifest=manifest, state=PluginState.DISABLED)
        else:
            record.manifest = manifest
        self.kernel.events.emit("plugins.changed", manifest.id)
        logger.info("插件已安装: %s -> %s", manifest.id, dest)
        return manifest

    def _manifest_of(self, plugin_id: str) -> PluginManifest | None:
        for manifest in self.discover():
            if manifest.id == plugin_id:
                return manifest
        return None

    def get(self, plugin_id: str) -> PluginRecord | None:
        return self._records.get(plugin_id)

    def all(self) -> list[PluginRecord]:
        return list(self._records.values())

    @property
    def records(self) -> dict[str, PluginRecord]:
        return self._records
