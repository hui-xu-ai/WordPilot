import os, sys, json, zipfile, tempfile, pathlib
sys.path.insert(0, "src")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
app = QApplication([])
from core.kernel import Kernel
k = Kernel(); k.boot()

# 造一个假插件 zip
tmp = tempfile.mkdtemp()
pkgdir = pathlib.Path(tmp) / "hello"
pkgdir.mkdir()
(pkgdir / "plugin.json").write_text(json.dumps({
    "id": "hello", "name": "Hello", "version": "0.1",
    "entry": "plugin:HelloPlugin", "requires": [], "deps": []}), encoding="utf-8")
(pkgdir / "plugin.py").write_text(
    "from core.sdk.plugin_base import PluginBase\n"
    "class HelloPlugin(PluginBase):\n"
    "    def activate(self, ctx):\n"
    "        super().activate(ctx)\n"
    "        ctx.logger.info('hello activated')\n", encoding="utf-8")
zpath = pathlib.Path(tmp) / "hello.zip"
with zipfile.ZipFile(zpath, "w") as zf:
    zf.write(pkgdir / "plugin.json", "hello/plugin.json")
    zf.write(pkgdir / "plugin.py", "hello/plugin.py")

pm = k.plugins
m = pm.install(zpath)
print("installed:", m.id, "| state:", pm.get("hello").state)
assert pm.get("hello").state == "disabled", "安装后应默认未启用"

# 勾选后应能加载（用户插件走文件位置导入）
pm.set_enabled("hello", True)
rec = pm.get("hello")
print("after enable:", rec.state, "| error:", rec.error)
assert rec.state == "active", rec.error

# 取消勾选卸载
pm.set_enabled("hello", False)
print("after disable:", pm.get("hello").state)
assert pm.get("hello").state == "disabled"

# 重启（新 Kernel）后：未启用的用户插件不应自动加载
k2 = Kernel(); k2.boot(); k2.plugins.load_all()
rec2 = k2.plugins.get("hello")
print("reboot state:", rec2.state if rec2 else None)
assert rec2 is not None and rec2.state == "disabled"

# 清理测试插件与配置
import shutil
pass
from core.plugin_manager import USER_PLUGINS_DIR
shutil.rmtree(USER_PLUGINS_DIR / "hello", ignore_errors=True)
k.config.set("plugins", {})
print("INSTALL OK")
