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
import re
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

#: 插件 id 必须是安全目录名：安装时会 shutil.rmtree/copytree 到 USER_PLUGINS_DIR/<id>
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


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
    def _sort_by_deps(self, manifests: list[PluginManifest]) -> tuple[list[PluginManifest], dict[str, str]]:
        """拓扑排序；**坏插件只记失败，不牵连其余插件**。

        返回 ``(ordered, failed)``：``failed`` 是 id -> 原因。历史实现在这里 ``raise``，
        再被 ``load_all`` 变成 ``return []``——一个插件写错 deps 就让全部内置插件不加载。
        """
        by_id = {m.id: m for m in manifests}
        ordered: list[PluginManifest] = []
        failed: dict[str, str] = {}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(manifest: PluginManifest, chain: list[str]) -> None:
            if manifest.id in visited or manifest.id in failed:
                return
            if manifest.id in visiting:
                for pid in chain + [manifest.id]:
                    failed.setdefault(pid, f"插件依赖存在环: {manifest.id}")
                return
            visiting.add(manifest.id)
            ok = True
            for dep in manifest.deps:
                if dep not in by_id:
                    failed[manifest.id] = f"依赖的插件不存在: {dep}"
                    ok = False
                    break
                visit(by_id[dep], chain + [manifest.id])
                if dep in failed:
                    failed[manifest.id] = f"依赖插件 [{dep}] 不可用: {failed[dep]}"
                    ok = False
                    break
            visiting.discard(manifest.id)
            if ok and manifest.id not in failed:
                visited.add(manifest.id)
                ordered.append(manifest)

        for manifest in manifests:
            visit(manifest, [])
        return ordered, failed

    # ---------------- 加载 ----------------
    def load_all(self) -> list[PluginRecord]:
        """加载全部可加载插件；依赖有问题的插件单独记 FAILED，其余照常加载。"""
        manifests = self.discover()
        ordered, failed = self._sort_by_deps(manifests)
        records: list[PluginRecord] = []
        by_id = {m.id: m for m in manifests}
        for plugin_id, reason in failed.items():
            manifest = by_id.get(plugin_id)
            if manifest is None:
                continue
            record = self._records.setdefault(plugin_id, PluginRecord(manifest=manifest))
            record.state = PluginState.FAILED
            record.error = reason
            record.instance = None
            logger.error("插件 [%s] 依赖校验失败：%s（其余插件照常加载）", plugin_id, reason)
            records.append(record)
        records.extend(self.load(manifest) for manifest in ordered)
        return records

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

        plugin_dir = self._plugin_dir(manifest)
        package_name = f"user_plugins.{manifest.id}"

        # S4：升级后磁盘文件换了，但 sys.modules 里还是旧模块——先清掉这个插件的
        # 全部模块，下面的 exec_module 才会读到新代码。历史上只能重启程序。
        self._purge_user_modules(package_name)

        # S3：外装多文件插件要能 `from .helper import X`，所以必须先把
        # `user_plugins` 与 `user_plugins.<id>` 两个包建出来并设 __path__。
        self._ensure_user_package(package_name, plugin_dir)

        module_name = f"{package_name}.{manifest.entry_module}"
        entry_file = plugin_dir / f"{manifest.entry_module}.py"
        spec = importlib.util.spec_from_file_location(module_name, entry_file)
        if spec is None or spec.loader is None:
            raise PluginError(f"插件入口不可加载: {entry_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _purge_user_modules(package_name: str) -> None:
        """删掉 `<package>` 及其子模块的缓存（升级/重装后强制读新文件）。"""
        for name in [n for n in sys.modules
                     if n == package_name or n.startswith(package_name + ".")]:
            sys.modules.pop(name, None)

    @staticmethod
    def _ensure_user_package(package_name: str, plugin_dir: Path) -> None:
        """建立 user_plugins / user_plugins.<id> 包对象，使相对导入可用（S3）。"""
        import types

        if "user_plugins" not in sys.modules:
            top = types.ModuleType("user_plugins")
            top.__path__ = []                      # 标记为包，避免被当成普通模块
            sys.modules["user_plugins"] = top
        package = types.ModuleType(package_name)
        package.__path__ = [str(plugin_dir)]
        package.__package__ = package_name
        sys.modules[package_name] = package

    def load(self, manifest: PluginManifest, force: bool = False) -> PluginRecord:
        """加载单个插件。force=True 时忽略启用状态（用于将来从界面手动启用）。"""
        record = self._records.setdefault(manifest.id, PluginRecord(manifest=manifest))

        if not force and not self._is_enabled(manifest):
            record.state = PluginState.DISABLED
            record.instance = None
            logger.info("插件未启用，已跳过: %s", manifest.id)
            return record

        # S5：拓扑排序只保证"先加载依赖"，但依赖可能加载失败/被禁用。
        # 这里在实例化之前再看一眼依赖记录，避免依赖方照样显示 ACTIVE。
        for dep in manifest.deps:
            dep_record = self._records.get(dep)
            dep_state = getattr(dep_record, "state", None)
            if dep_state != PluginState.ACTIVE:
                record.state = PluginState.FAILED
                record.error = f"依赖插件 [{dep}] 未激活（{dep_state or '未加载'}）"
                logger.error("插件 [%s] 加载失败：%s", manifest.id, record.error)
                return record

        # 插件词条要在 activate() 之前就绪，否则菜单 label 无法翻译
        self._register_plugin_i18n(manifest)

        missing = [name for name in manifest.requires if name not in self.kernel.services]
        if missing:
            record.state = PluginState.FAILED
            record.error = f"缺少内核服务: {', '.join(missing)}"
            logger.error("插件 [%s] 加载失败：%s", manifest.id, record.error)
            return record

        instance = None
        try:
            module = self._import_entry_module(manifest)
            plugin_cls = getattr(module, manifest.entry_class)
            instance = plugin_cls()
            instance.activate(PluginContext(self.kernel, manifest))
            self._register_plugin_assets(manifest, instance)
        except Exception as exc:
            record.state = PluginState.FAILED
            record.error = str(exc)
            record.instance = None
            logger.exception("插件 [%s] 激活失败", manifest.id)
            # 补偿：activate 半途抛异常时，UI 条目/热键/订阅/i18n 可能已注册成功
            if instance is not None:
                try:
                    instance.deactivate()
                except Exception:
                    logger.exception("插件 [%s] 失败补偿 deactivate 异常", manifest.id)
            self._cleanup_partial(manifest.id)
            self._notify_pet(manifest.id, False)
            return record

        record.instance = instance
        record.state = PluginState.ACTIVE
        record.error = None
        self._notify_pet(manifest.id, True)
        logger.info("插件已加载: %s v%s", manifest.id, manifest.version)
        return record

    @staticmethod
    def _notify_pet(plugin_id: str, active: bool) -> None:
        """告诉宠物服务该插件是否在跑：右键快捷项据此自动显示/隐藏。"""
        try:
            from core.services.pet_service import PetService

            PetService.instance().set_plugin_active(plugin_id, active)
        except Exception:                      # noqa: BLE001 - 宠物不可用不影响插件加载
            logger.debug("通知宠物服务失败（%s）", plugin_id, exc_info=True)

    @staticmethod
    def _pet_cleanup(plugin_id: str) -> None:
        """卸载插件时清掉它注册的快捷项（重启后由插件重新注册）。"""
        try:
            from core.services.pet_service import PetService

            PetService.instance().unregister_plugin(plugin_id)
        except Exception:                      # noqa: BLE001
            pass

    def _register_plugin_assets(self, manifest: PluginManifest, instance) -> None:
        """把插件声明的**用户资产**登记给清理服务（`PluginBase.assets()`）。

        与清理模块解耦：清理侧只认登记表，不认识任何插件；插件卸载时自动撤销。
        """
        provider = getattr(instance, "assets", None)
        if not callable(provider):
            return
        try:
            from core.services import housekeeping

            housekeeping.register_asset_source(manifest.id, provider)
        except Exception:                 # noqa: BLE001 - 资产登记失败不该挡住插件激活
            logger.exception("插件 [%s] 资产登记失败", manifest.id)

    def _cleanup_partial(self, plugin_id: str) -> None:
        """activate 失败后的补偿清理：把可能已注册的贡献点全部收回（best-effort）。"""
        try:
            from core.services import housekeeping

            housekeeping.unregister_asset_source(plugin_id)
        except Exception:                 # noqa: BLE001
            logger.exception("插件 [%s] 补偿撤销资产登记失败", plugin_id)
        try:
            self.kernel.ui.remove_plugin(plugin_id)
        except Exception:
            logger.exception("插件 [%s] 补偿清理 UI 失败", plugin_id)
        hotkeys = self.kernel.services.get("hotkeys")
        if hotkeys is not None:
            try:
                hotkeys.unregister_owner(plugin_id)
                hotkeys.apply()
            except Exception:
                logger.exception("插件 [%s] 补偿清理热键失败", plugin_id)
        i18n = self.kernel.services.get("i18n")
        if i18n is not None:
            try:
                i18n.unregister_namespace(plugin_id)
            except Exception:
                logger.exception("插件 [%s] 补偿清理语言包失败", plugin_id)

    # ---------------- 卸载 ----------------
    def unload(self, plugin_id: str) -> None:
        self._notify_pet(plugin_id, False)
        self._pet_cleanup(plugin_id)
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
        instance = getattr(record, "instance", None)
        if instance is not None:
            try:
                instance.deactivate()
            except Exception:
                logger.exception("插件 [%s] 停用异常", plugin_id)
        try:
            from core.services import housekeeping

            housekeeping.unregister_asset_source(plugin_id)   # 插件停用 → 撤销资产声明
        except Exception:                 # noqa: BLE001
            logger.exception("插件 [%s] 撤销资产登记失败", plugin_id)
        i18n = self.kernel.services.get("i18n")
        if i18n is not None:
            i18n.unregister_namespace(plugin_id)
        hotkeys = self.kernel.services.get("hotkeys")
        if hotkeys is not None:
            hotkeys.unregister_owner(plugin_id)
            # 解绑后必须重建绑定：否则组合键仍挂在旧句柄上（幽灵热键，且无法复用同一组合键）
            hotkeys.apply()
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
                if record.state == PluginState.FAILED:
                    # 失败插件的错误信息不能静默丢掉：先记日志，再补偿清理残留后置为 DISABLED
                    logger.warning("插件 [%s] 处于失败态（%s），清理残留后置为 DISABLED",
                                   plugin_id, record.error)
                    self._cleanup_partial(plugin_id)
                record.state = PluginState.DISABLED
                record.error = None
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
            # id 会被当成目录名做 rmtree/copytree：必须是安全目录名且不得越出用户插件目录
            if not _ID_PATTERN.match(manifest.id):
                raise PluginError(
                    f"插件 id 非法（只允许字母/数字/下划线/连字符）: {manifest.id!r}")
            dest = USER_PLUGINS_DIR / manifest.id
            if dest.resolve().parent != USER_PLUGINS_DIR.resolve():
                raise PluginError(f"插件 id 会越出用户插件目录: {manifest.id!r}")
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
