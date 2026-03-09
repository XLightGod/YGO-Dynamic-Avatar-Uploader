import asyncio
import json
import os
import shutil
import sys
import threading
import customtkinter as ctk
from tkinter import filedialog, messagebox
from customtkinter import CTkImage
from PIL import Image, ImageSequence
import winreg
from mitmproxy import http, options
from mitmproxy.tools.dump import DumpMaster
from pathlib import Path
import subprocess

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

class AvatarReplacer:
    def __init__(self, image_bytes):
        self.image_bytes = image_bytes
        print("脚本已启动，等待上传请求")

    def request(self, flow: http.HTTPFlow):
        if "appsevice.windoent.com/upload" not in flow.request.pretty_url:
            return

        try:
            content_type = flow.request.headers.get("Content-Type", "")
            boundary = content_type.split("boundary=")[-1]
            boundary_bytes = b"--" + boundary.encode()
            raw = flow.request.raw_content

            start_marker = b"Content-Type: image/"
            start_index = raw.find(start_marker)
            image_start = raw.find(b"\r\n\r\n", start_index) + 4
            image_end = raw.find(boundary_bytes, image_start) - 2
            new_body = raw[:image_start] + self.image_bytes + raw[image_end:]
            flow.request.raw_content = new_body
            flow.request.headers["Content-Length"] = str(len(new_body))
            print("头像已替换，正在上传")
        except Exception as e:
            print(f"替换失败：{e}")

    def response(self, flow: http.HTTPFlow):
        if "appsevice.windoent.com/upload" in flow.request.pretty_url:
            if flow.response.status_code == 200:
                try:
                    data = flow.response.json()
                    if data.get("msg") == "fail":
                        print("替换失败，请重试。")
                    else:
                        print("替换成功。")
                except Exception:
                    print("替换失败，请重试。")
            else:
                print("替换失败，请重试。")
        elif "yugiohmatchapi.windoent.com/v1/notice/center/unread" in flow.request.pretty_url\
            or "yugiohmatchapi.windoent.com/v1/news" in flow.request.pretty_url:
            if flow.response is None:
                return
            
            try:
                body = flow.response.get_text()
                data = json.loads(body or "{}")
            except Exception:
                return

            data["code"] = 10314
            data["msg"] = "您已经在别的设配登录了"
            if "data" not in data:
                data["data"] = {}

            flow.response.text = json.dumps(data, ensure_ascii=False)
            #triggered = True   # 只改这一次


def set_windows_proxy(enable=True):
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                         0, winreg.KEY_ALL_ACCESS)
    if enable:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, "127.0.0.1:7410")
    else:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
    winreg.CloseKey(key)

class ProxyGUI:
    def __init__(self, root):
        self.root = root
        root.title("一起来决斗头像替换工具")
        self.image_bytes = None
        self.proxy_task = None
        self.gif_frames = []
        self.current_frame = 0
        self.gif_running = False
        self.gif_after_id = None

        # 固定大小头像框
        self.avatar_frame = ctk.CTkFrame(root, width=128, height=128)
        self.avatar_frame.pack_propagate(False)
        self.avatar_frame.pack(pady=10)

        # 占位图
        placeholder = Image.new("RGBA", (128, 128), (240, 240, 240, 255))
        self.placeholder_image = CTkImage(light_image=placeholder, size=(128, 128))
        self.avatar_label = ctk.CTkLabel(self.avatar_frame, image=self.placeholder_image, text="")
        self.avatar_label.pack()

        # 按钮区域
        btn_frame = ctk.CTkFrame(root, fg_color="transparent")
        btn_frame.pack(pady=10)

        self.choose_btn = ctk.CTkButton(btn_frame, text="选择头像", command=self.choose_avatar, width=100)
        self.choose_btn.pack(side="left", padx=5)

        self.start_btn = ctk.CTkButton(btn_frame, text="启动代理", command=self.start_proxy, width=100)
        self.start_btn.pack(side="left", padx=5)

        self.stop_btn = ctk.CTkButton(btn_frame, text="停止代理", command=self.stop_proxy, width=100)
        self.stop_btn.pack(side="left", padx=5)

        # 日志输出框（自定义滚动条）
        self.log_frame = ctk.CTkFrame(self.root)
        self.log_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.log_box = ctk.CTkTextbox(self.log_frame, height=200)
        self.log_box.pack(side="left", fill="both", expand=True)

        self.log_scrollbar = ctk.CTkScrollbar(self.log_frame, command=self.log_box.yview)
        self.log_scrollbar.pack(side="right", fill="y")

        self.log_box.configure(yscrollcommand=self.log_scrollbar.set)

        self._patch_print()

    def _patch_print(self):
        import builtins
        old_print = print

        def new_print(*args, **kwargs):
            old_print(*args, **kwargs)
            msg = " ".join(map(str, args))
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")

        builtins.print = new_print

    def choose_avatar(self):
        path = filedialog.askopenfilename(
            title="选择你的头像文件（最大3MB）",
            filetypes=[("图片文件", "*.gif;*.png;*.jpg;*.jpeg;*.webp"), ("所有文件", "*.*")]
        )
        if not path:
            return

        if os.path.getsize(path) > 3 * 1024 * 1024:
            messagebox.showwarning("文件过大", "文件大小不能超过3MB！")
            return

        self.image_bytes = open(path, "rb").read()

        # 显示头像图片
        try:
            img = Image.open(path)
            self.gif_frames = []
            self.gif_durations = []

            # 停止之前的动画
            if self.gif_after_id:
                self.root.after_cancel(self.gif_after_id)
                self.gif_after_id = None
            self.gif_running = False

            if getattr(img, "is_animated", False):
                for frame in ImageSequence.Iterator(img):
                    duration = frame.info.get("duration", 100)
                    frame = frame.convert("RGBA").resize((128, 128), Image.LANCZOS)
                    self.gif_frames.append(CTkImage(light_image=frame, size=(128, 128)))
                    self.gif_durations.append(duration)
                self.current_frame = 0
                self.gif_running = True
                self._animate_gif()
            else:
                img = img.convert("RGBA").resize((128, 128), Image.LANCZOS)
                photo = CTkImage(light_image=img, size=(128, 128))
                self.avatar_label.configure(image=photo)
                self.gif_running = False

            print(f"已选择头像：{os.path.basename(path)}")
            if self.proxy_task and self.proxy_task.is_alive():
                self.stop_proxy()
                self.start_proxy()

        except Exception as e:
            messagebox.showerror("错误", f"无法加载图片：{e}")

    def _animate_gif(self):
        if self.gif_running and self.gif_frames:
            frame = self.gif_frames[self.current_frame]
            self.avatar_label.configure(image=frame)
            self.avatar_label.image = frame
            delay = self.gif_durations[self.current_frame]
            self.current_frame = (self.current_frame + 1) % len(self.gif_frames)
            self.gif_after_id = self.root.after(delay, self._animate_gif)

    def start_proxy(self):
        if self.proxy_task and self.proxy_task.is_alive():
            messagebox.showinfo("代理运行中", "代理已经在运行了。")
            return
         
        if not self.image_bytes:
            messagebox.showwarning("未选择头像", "请先选择头像文件！")
            return

        def run():
            asyncio.run(self._run_proxy())

        self.proxy_task = threading.Thread(target=run, daemon=True)
        self.proxy_task.start()

    async def _run_proxy(self):
        set_windows_proxy(True)
        opts = options.Options(listen_host="127.0.0.1", listen_port=7410)
        self.mitm = DumpMaster(opts, with_termlog=False, with_dumper=False)
        addon = AvatarReplacer(self.image_bytes)
        self.mitm.addons.add(addon)
        try:
            await self.mitm.run()
        except Exception as e:
            print(f"代理错误：{e}")

    def stop_proxy(self):
        if hasattr(self, "mitm") and self.mitm.running():
            try:
                self.mitm.shutdown()
                print("代理已停止")
            except Exception as e:
                print(f"停止失败：{e}")
        set_windows_proxy(False)


mitmproxy_dir = os.path.join(os.path.expanduser("~"), ".mitmproxy")

def center_window(window, width=400, height=400):
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    x = (screen_width - width) // 2
    y = (screen_height - height) // 2
    window.geometry(f"{width}x{height}+{x}+{y}")

def resource_path(relative_path):
    """获取资源的绝对路径，兼容 PyInstaller 打包"""
    base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))
    return os.path.join(base_path, relative_path)

def ensure_mitmproxy_cert():
    cert_names = [
        "mitmproxy-ca.pem",
        "mitmproxy-ca-cert.p12",
        "mitmproxy-ca-cert.pem",
        "mitmproxy-dhparam.pem"
    ]
    os.makedirs(mitmproxy_dir, exist_ok=True)
    
    for name in cert_names:
        src = os.path.join(resource_path("certs"), name)
        dst = os.path.join(mitmproxy_dir, name)
        shutil.copyfile(src, dst)

    subprocess.run([
        "certutil", "-addstore", "-f", "ROOT", os.path.join(mitmproxy_dir, cert_names[2])
    ], shell=True)

if __name__ == '__main__':
    root = ctk.CTk()
    icon_path = resource_path("icon.ico")
    root.iconbitmap(icon_path)
    center_window(root, 400, 400)
    app = ProxyGUI(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.stop_proxy(), root.destroy()))
    ensure_mitmproxy_cert()
    root.mainloop()
