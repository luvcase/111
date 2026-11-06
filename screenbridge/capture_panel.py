"""A mouse-operated capture keypad; it does not inject operating-system keys."""


class CapturePanel:
    def __init__(self, root, submit):
        self.root = root
        self.submit = submit
        self.window = None
        self.wanted = False
        self.suspended = False
        self.buttons = {}

    def show(self):
        import tkinter as tk

        if self.window is None:
            self.window = tk.Toplevel(self.root)
            self.window.title("ScreenBridge · 截图虚拟按键")
            self.window.resizable(False, False)
            self.window.attributes("-topmost", True)
            self.window.configure(background="#f3f7f5")
            self.window.protocol("WM_DELETE_WINDOW", self.close)
            tk.Label(
                self.window,
                text="点击按键即可截图并提交 AI",
                background="#f3f7f5",
                foreground="#246d51",
                padx=16,
                pady=10,
            ).pack()
            keys = tk.Frame(self.window, background="#f3f7f5")
            keys.pack(padx=12)
            for column, (kind, text) in enumerate((("full", "全屏截图"), ("region", "区域截图"))):
                button = tk.Button(
                    keys,
                    text=text,
                    command=lambda kind=kind: self.submit(kind, source="panel"),
                    background="#246d51",
                    foreground="white",
                    activebackground="#1e5843",
                    activeforeground="white",
                    width=12,
                    padx=10,
                    pady=14,
                )
                button.grid(row=0, column=column, padx=4, pady=4)
                self.buttons[kind] = button
            self.status = tk.Label(
                self.window,
                text="等待截图",
                background="#f3f7f5",
                foreground="#53675e",
                wraplength=300,
                padx=12,
                pady=10,
            )
            self.status.pack()
        self.wanted = True
        if not self.suspended:
            self.window.deiconify()
            self.window.lift()

    def close(self):
        self.wanted = False
        if self.window is not None:
            self.window.withdraw()

    def suspend(self):
        self.suspended = True
        if self.window is not None:
            self.window.withdraw()
            self.root.update_idletasks()

    def resume(self):
        self.suspended = False
        if self.wanted:
            self.show()

    def update_status(self, values):
        if self.window is not None:
            self.status.configure(text=values.get("last_error") or values["last_capture"])
