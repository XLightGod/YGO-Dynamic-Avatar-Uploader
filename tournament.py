import asyncio
import json
import os
import shutil
import sys
import threading
import customtkinter as ctk
from tkinter import messagebox
import winreg
from mitmproxy import http, options
from mitmproxy.tools.web.master import WebMaster
from pathlib import Path
import subprocess

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

class ProxyAddon:
    def __init__(self):
        print("脚本已启动，等待请求")

    MOBILE_UA = (
        "Mozilla/5.0 (Linux; Android 12; M2012K11AC Build/SKQ1.211006.001; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/122.0.6261.120 "
        "Mobile Safari/537.36 XWEB/1220133 MMWEBSDK/20240404 MMWEBID/8518 "
        "MicroMessenger/8.0.49.2600(0x2800313D) WeChat/arm64 Weixin NetType/WIFI "
        "Language/zh_CN ABI/arm64 MiniProgramEnv/android"
    )

    def request(self, flow: http.HTTPFlow):
        if "yugiohmatchapi" in flow.request.pretty_host:
            flow.request.headers["User-Agent"] = self.MOBILE_UA

    def response(self, flow: http.HTTPFlow):
        if "/v1/news" in flow.request.pretty_url:
            if flow.response is None:
                return

            try:
                body = flow.response.get_text()
                data = json.loads(body or "{}")
                data["code"] = 10314
                data["msg"] = "您已经在别的设配登录了"
                if "data" not in data:
                    data["data"] = {}
            except Exception:
                return           

            flow.response.text = json.dumps(data, ensure_ascii=False)
        elif "/v1/match/info/" in flow.request.pretty_url:
            if flow.response is None:
                return

            try:
                body = flow.response.get_text()
                data = json.loads(body or "{}")
                bottom = data["data"]["bottom"]
                bottom["type"] = 0
                bottom["title"]["text"] = ""
            except Exception:
                return

            flow.response.text = json.dumps(data, ensure_ascii=False)


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
        root.title("代理工具")
        self.proxy_task = None

        # 按钮区域
        btn_frame = ctk.CTkFrame(root, fg_color="transparent")
        btn_frame.pack(pady=10)

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

    def start_proxy(self):
        if self.proxy_task and self.proxy_task.is_alive():
            messagebox.showinfo("代理运行中", "代理已经在运行了。")
            return

        def run():
            asyncio.run(self._run_proxy())

        self.proxy_task = threading.Thread(target=run, daemon=True)
        self.proxy_task.start()

    async def _run_proxy(self):
        set_windows_proxy(True)
        opts = options.Options(listen_host="127.0.0.1", listen_port=7410)
        self.mitm = WebMaster(opts)
        addon = ProxyAddon()
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
