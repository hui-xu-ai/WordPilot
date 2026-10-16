import math
import os
import shutil
import tempfile
import time
import zipfile

import numpy as np
import piexif
import win32clipboard
import win32com.client
from PIL import Image

from core.services.word_constants import wdConst  # Word 常量字典

import ctypes


class WordImageProcessor:
    def __init__(self, config_dir):
        self.user32 = ctypes.windll.user32  # 新增

        self.config_dir = config_dir
        self.temp_docx_path = os.path.join(config_dir, "Image.docx")
        if not os.path.exists(self.temp_docx_path):
            raise FileNotFoundError(f"请在config文件夹下建立可以无损保存图片的Image.docx文件: {self.temp_docx_path}")
        self.word_app = None
        self.work_doc = None
        self._open_word_app()

        # ★ 保存用户当前的活动文档（用于稍后激活遮挡）
        try:
            self.user_active_doc = self.word_app.ActiveDocument
        except Exception:
            self.user_active_doc = None  # 如果用户没打开文档，可能为 None

        self._open_workspace()



    def _open_word_app(self):
        try:
            self.word_app = win32com.client.GetObject(Class="Word.Application")
        except Exception:
            raise RuntimeError("未检测到 Word 应用程序，请先打开 Word")

    def _open_workspace(self):
        user32 = self.user32  # 在 __init__ 中定义的 ctypes.windll.user32
        try:
            # 1. 停止 Word 自身重绘
            self.word_app.ScreenUpdating = False
            # 2. 锁定整个桌面，冻结所有窗口绘制
            user32.LockWindowUpdate(user32.GetDesktopWindow())

            # 3. 打开工作文档（此时因为桌面锁定，任何绘制都不会显示）
            self.work_doc = self.word_app.Documents.Open(
                FileName=self.temp_docx_path, Visible=False
            )

            # 4. 立即将窗口移出可视区域并缩至 1×1 像素
            if self.work_doc.Windows.Count > 0:
                win = self.work_doc.Windows(1)
                hwnd = win.Hwnd
                # 参数说明：句柄、Z序位置、x=-20000、y=-20000、宽度=1、高度=1、标志
                user32.SetWindowPos(
                    hwnd, 0, -20000, -20000, 1, 1,
                    0x0004 | 0x0010  # SWP_NOZORDER | SWP_NOACTIVATE
                )
                # 5. 系统级隐藏（任务栏也不会显示）
                user32.ShowWindow(hwnd, 0)  # SW_HIDE
                # 6. Word 模型级隐藏
                win.Visible = False

            # 7. 【可选】如果用户原本有打开的文档，立即激活它来覆盖（进一步加强）
            if self.user_active_doc is not None:
                try:
                    self.user_active_doc.Activate()
                except Exception:
                    pass

        except Exception as e:
            raise RuntimeError(f"无法打开工作文档: {e}")
        finally:
            # 8. 解锁桌面 + 恢复 Word 刷新
            user32.LockWindowUpdate(0)
            self.word_app.ScreenUpdating = True

    def _hide_work_doc_window(self):
        """当工作文档被意外显示时，强制再次隐藏并移出屏幕"""
        if not self.work_doc:
            return
        try:
            if self.work_doc.Windows.Count > 0:
                win = self.work_doc.Windows(1)
                hwnd = win.Hwnd
                self.user32.SetWindowPos(hwnd, 0, -20000, -20000, 0, 0, 0x0001 | 0x0010)
                self.user32.ShowWindow(hwnd, 0)  # SW_HIDE
                win.Visible = False
        except Exception:
            pass

    def close(self):
        try:
            if self.work_doc:
                self.work_doc.Close(SaveChanges=False)
                self.work_doc = None
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ---------- 提取 ----------
    def _get_selected_shape(self):
        try:
            sel = self.word_app.Selection
            if sel.Type == wdConst.wdSelectionShape:
                shape = sel.ShapeRange(1)
                is_inline = False
                anchor = shape.Anchor
                info = {
                    'left': shape.Left,
                    'top': shape.Top,
                    'width': shape.Width,
                    'height': shape.Height,
                    'anchor_start': anchor.Start
                }
            elif sel.Type == wdConst.wdSelectionInlineShape:
                shape = sel.InlineShapes(1)
                is_inline = True
                info = {
                    'range_start': shape.Range.Start,
                    'range_end': shape.Range.End,
                    'width': shape.Width,
                    'height': shape.Height
                }
            else:
                raise RuntimeError("未选中图片，请点击 Word 中的图片")
            return shape, is_inline, info
        except Exception as e:
            raise RuntimeError(f"获取选中图片失败: {e}")

    def _clear_work_doc(self):
        try:
            rng = self.work_doc.Range()
            rng.WholeStory()
            rng.Delete()
        except:
            pass

    def _wait_clipboard_image(self, timeout=2.0):  # 识别图片有无复制成功
        """轮询剪贴板直到出现图片格式，返回 True 成功，False 超时"""
        import time as _time
        start = _time.time()
        while _time.time() - start < timeout:
            try:
                win32clipboard.OpenClipboard()
                # Word 复制图片通常提供 DIB 位图格式
                if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_DIB):
                    win32clipboard.CloseClipboard()
                    return True
                win32clipboard.CloseClipboard()
            except Exception:
                pass
            _time.sleep(0.004)  # 10ms 轮询一次，不占 CPU
        return False

    def _wait_until_doc_has_image(self, timeout=1.0):  # 识别图片有无粘贴成功
        start = time.time()
        while time.time() - start < timeout:
            if self.work_doc.InlineShapes.Count + self.work_doc.Shapes.Count > 0:
                return True
            time.sleep(0.004)  # 5ms 轮询，极致快速
        return False

    def _extract_original_from_shape(self, shape):
        # t0 = time.time()    #  计时

        # 1. 选中并复制
        shape.Select()
        self.word_app.Selection.Copy()
        # 2. 等待剪贴板中出现图片数据（最多等待 2 秒，防止死循环）
        if not self._wait_clipboard_image(timeout=2.0):
            raise TimeoutError("剪贴板超时未检测到图片，可能复制失败")

        # t1 = time.time()    #  计时

        # 3. 粘贴到工作文档（此时剪贴板已就绪）

        # 清除并粘贴前，再次确保工作文档窗口隐藏
        self._hide_work_doc_window()

        self._clear_work_doc()
        self.work_doc.Range().PasteAndFormat(wdConst.wdFormatOriginalFormatting)

        # t2 = time.time()    #  计时

        self._wait_until_doc_has_image(timeout=1.0)  # 粘贴成功识别

        # t3 = time.time()    #  计时

        # 保存前再次隐藏
        self._hide_work_doc_window()
        self.work_doc.Save()

        # t4 = time.time()    #  计时

        extract_dir = tempfile.mkdtemp(prefix="img_extract_")
        with zipfile.ZipFile(self.temp_docx_path, 'r') as z:
            media_files = [n for n in z.namelist() if n.startswith("word/media/")]
            if not media_files:
                raise ValueError("Image.docx 中未找到图片")
            target = media_files[-1]
            ext = os.path.splitext(target)[1]
            original_path = os.path.join(extract_dir, f"original{ext}")
            with z.open(target) as src, open(original_path, 'wb') as dst:
                shutil.copyfileobj(src, dst)

        # print(f"复制图片耗时：{(t1-t0)*1000:.2f} 毫秒\n")  #  计时
        # print(f"粘贴图片耗时：{(t2-t1)*1000:.2f} 毫秒\n")  #  计时
        # print(f"粘贴等待耗时：{(t3-t2)*1000:.2f} 毫秒\n")  #  计时
        # print(f"保存图片耗时：{(t4-t3)*1000:.2f} 毫秒\n")  #  计时

        return original_path

    # ---------- 图像处理 ----------
    @staticmethod
    def _color_distance(c1, c2):
        return sum((a - b) ** 2 for a, b in zip(c1, c2)) ** 0.5

    def _detect_content_bounds(self, pil_img, bg_color, threshold=30):
        """
        使用 NumPy 向量化计算，直接在全分辨率上检测内容边界。
        - 100% 原始精度，无缩放误差。
        - 速度极快：千万像素图片仅需约 50ms。
        """
        # 转换为 RGB 模式，避免模式不一
        if pil_img.mode != 'RGB':
            pil_img = pil_img.convert('RGB')

        # 转为 NumPy 数组 (h, w, 3)
        arr = np.array(pil_img, dtype=np.int16)
        h, w, _ = arr.shape

        # 背景色转为数组
        bg = np.array(bg_color, dtype=np.int16)

        # 计算每个像素与背景的欧氏距离平方
        diff = arr - bg
        dist_sq = np.sum(diff ** 2, axis=2)  # shape (h, w)

        # 生成内容掩码：距离平方 > 阈值平方 即为内容像素
        thr_sq = threshold ** 2
        mask = dist_sq > thr_sq  # 布尔数组，True 表示内容

        # 若无内容像素，返回空白标记
        if not np.any(mask):
            return 0, 0, -1, -1

        # 分别对行、列求 “是否包含内容像素”
        rows = np.any(mask, axis=1)  # shape (h,)
        cols = np.any(mask, axis=0)  # shape (w,)

        # 找到 True 区域的起始和结束索引
        top = int(np.argmax(rows))  # 第一个 True 的行号
        bottom = int(h - 1 - np.argmax(rows[::-1]))  # 最后一个 True 的行号
        left = int(np.argmax(cols))  # 第一个 True 的列号
        right = int(w - 1 - np.argmax(cols[::-1]))  # 最后一个 True 的列号

        return left, top, right, bottom

    def _apply_adjustments(self, src_path, dst_path, width_mode, height_mode,
                           margin_w, margin_h, bg_color,
                           margin_w_unit='px', margin_h_unit='px'):
        img = Image.open(src_path)
        fmt = img.format or 'PNG'
        orig_dpi = img.info.get('dpi', (72, 72))
        exif_bytes = img.info.get('exif')

        # if img.mode != 'RGB':
        # img = img.convert('RGB')

        left, top, right, bottom = self._detect_content_bounds(img, bg_color)
        if right < left:
            raise ValueError("图片内容全为空白")

        content_w = right - left + 1
        content_h = bottom - top + 1
        orig_w, orig_h = img.size

        # 根据单位将边缘距离转换为像素（仅在裁剪模式下生效）
        if width_mode == 'crop':
            if margin_w_unit == 'pct':
                margin_w = math.ceil(content_w * margin_w / 100.0)
            else:
                margin_w = int(margin_w)
        else:
            margin_w = 0

        if height_mode == 'crop':
            if margin_h_unit == 'pct':
                margin_h = math.ceil(content_h * margin_h / 100.0)
            else:
                margin_h = int(margin_h)
        else:
            margin_h = 0

        if width_mode == 'none':
            new_w, new_left = orig_w, left
        elif width_mode == 'crop':
            new_w = content_w + 2 * margin_w
            new_left = margin_w
        elif width_mode == 'center':
            new_w = orig_w
            total_margin = left + (orig_w - right - 1)
            new_left = total_margin // 2
        else:
            raise ValueError(f"无效宽度模式: {width_mode}")

        if height_mode == 'none':
            new_h, new_top = orig_h, top
        elif height_mode == 'crop':
            new_h = content_h + 2 * margin_h
            new_top = margin_h
        elif height_mode == 'center':
            new_h = orig_h
            total_margin = top + (orig_h - bottom - 1)
            new_top = total_margin // 2
        else:
            raise ValueError(f"无效高度模式: {height_mode}")

        if new_w < content_w or new_h < content_h:
            raise ValueError(f"边距过小，内容 {content_w}x{content_h} 无法放置")

        new_img = Image.new("RGB", (new_w, new_h), bg_color)
        content_box = (left, top, right + 1, bottom + 1)
        content_img = img.crop(content_box)
        new_img.paste(content_img, (new_left, new_top))

        new_img.info['dpi'] = orig_dpi
        save_kwargs = {'quality': 100, 'subsampling': 0, 'dpi': orig_dpi}
        if exif_bytes:
            try:
                exif_dict = piexif.load(exif_bytes)
                save_kwargs['exif'] = piexif.dump(exif_dict)
            except:
                pass
        new_img.save(dst_path, format=fmt, **save_kwargs)
        return new_img.size

    # ---------- 替换 ----------
    def _replace_shape(self, shape, is_inline, info, new_image_path, set_dimension='width'):
        self.word_app.ScreenUpdating = False
        try:
            try:
                self.word_app.Options.PictureCompression = False
                self.word_app.Options.DoNotCompressImages = True
            except Exception:
                pass

            shape.Delete()
            doc = self.word_app.ActiveDocument
            if is_inline:
                rng = doc.Range(info['range_start'], info['range_end'])
                new_shape = doc.InlineShapes.AddPicture(
                    FileName=new_image_path, LinkToFile=False,
                    SaveWithDocument=True, Range=rng)
            else:
                anc_range = doc.Range(info['anchor_start'], info['anchor_start'])
                new_shape = doc.Shapes.AddPicture(
                    FileName=new_image_path, LinkToFile=False,
                    SaveWithDocument=True, Anchor=anc_range)
                new_shape.Left = info['left']
                new_shape.Top = info['top']

            new_shape.LockAspectRatio = True
            if set_dimension == 'height':
                new_shape.Height = info['height']  # 高度和原图一样，宽度按比例
            elif set_dimension == 'width':
                new_shape.Width = info['width']  # 宽度和原图一样，高度按比例
            elif set_dimension == 'auto':
                pass  # 不设置任何尺寸，Word 会自动根据 DPI 计算
            self.word_app.ScreenRefresh()
        finally:
            self.word_app.ScreenUpdating = True

    # ---------- 主流程 ----------
    def process_selected(self, width_mode, height_mode,
                         margin_w=0, margin_h=0, bg_color=(255, 255, 255),
                         margin_w_unit='px', margin_h_unit='px'):
        """
        处理选中图片，返回 (success, message, orig_bytes, new_bytes)
        """
        output_filename = "cropped_output.png"
        orig_bytes = new_bytes = None
        original_path = None
        out_path = None
        try:
            # t0 = time.time()    #  计时

            shape, is_inline, info = self._get_selected_shape()
            original_path = self._extract_original_from_shape(shape)
            orig_bytes = os.path.getsize(original_path)

            out_path = os.path.join(self.config_dir, output_filename)

            # t1 = time.time()        #  计时

            self._apply_adjustments(original_path, out_path,
                                    width_mode, height_mode,
                                    margin_w, margin_h, bg_color,
                                    margin_w_unit, margin_h_unit)

            # t2 = time.time()        #  计时

            new_bytes = os.path.getsize(out_path)

            if (height_mode == 'crop') and (width_mode == 'crop'):
                set_dim = 'auto'  # 不缩放，按图片 DPI
            elif (height_mode == 'center') and (width_mode == 'crop'):
                set_dim = 'height'
            elif (height_mode == 'none') and (width_mode == 'crop'):
                set_dim = 'height'
            else:
                set_dim = 'width'

            self._replace_shape(shape, is_inline, info, out_path, set_dim)

            # t3 = time.time()        #  计时

            # print(f"提取图片耗时：{(t1-t0)*1000:.2f} 毫秒\n")  #  计时
            # print(f"识别边缘耗时：{(t2-t1)*1000:.2f} 毫秒\n")  #  计时
            # print(f"替换图片耗时：{(t3-t2)*1000:.2f} 毫秒\n")  #  计时
            # print(f"上面总共耗时：{(t3-t0)*1000:.2f} 毫秒\n")  #  计时

            return True, "处理完成", orig_bytes, new_bytes
        except Exception as e:
            return False, str(e), orig_bytes, new_bytes
        finally:
            for path in [original_path, out_path]:
                try:
                    if path and os.path.isfile(path):
                        os.remove(path)
                except Exception:
                    pass