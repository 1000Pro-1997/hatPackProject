#!/usr/bin/env python3
"""Small Tk GUI for the hotPack controller."""

from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


class IOSButton(tk.Canvas):
    def __init__(self, parent: tk.Widget, text: str, command, color: str):
        super().__init__(parent, height=48, background="#F2F2F7", highlightthickness=0, cursor="hand2")
        self.label = text
        self.command = command
        self.color = color
        self.state = "normal"
        self.bind("<Configure>", lambda _event: self.draw())
        self.bind("<Button-1>", self.clicked)

    def draw(self) -> None:
        self.delete("all")
        width, height, radius = max(self.winfo_width(), 20), 48, 12
        fill = self.color if self.state == "normal" else "#D1D1D6"
        text_color = "#FFFFFF" if self.state == "normal" else "#8E8E93"
        points = [
            radius, 1, width - radius, 1, width - 1, radius,
            width - 1, height - radius, width - radius, height - 1,
            radius, height - 1, 1, height - radius, 1, radius,
        ]
        self.create_polygon(points, smooth=True, splinesteps=24, fill=fill, outline="")
        self.create_text(width / 2, height / 2, text=self.label, fill=text_color, font=("Helvetica", 14, "bold"))

    def clicked(self, _event: tk.Event) -> None:
        if self.state == "normal":
            self.command()

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        state = kwargs.pop("state", None)
        if state is not None:
            self.state = str(state)
            self.configure(cursor="hand2" if self.state == "normal" else "arrow")
            self.draw()
        if cnf is not None or kwargs:
            return super().configure(cnf, **kwargs)
        return None


class IOSSlider(tk.Canvas):
    def __init__(self, parent: tk.Widget, variable: tk.IntVar, low: int, high: int, command):
        super().__init__(parent, height=32, background="#FFFFFF", highlightthickness=0, cursor="hand2")
        self.variable, self.low, self.high, self.command = variable, low, high, command
        self.bind("<Configure>", lambda _event: self.draw())
        self.bind("<Button-1>", self.move)
        self.bind("<B1-Motion>", self.move)

    def position(self) -> float:
        width = max(self.winfo_width(), 40)
        ratio = (self.variable.get() - self.low) / (self.high - self.low)
        return 14 + ratio * (width - 28)

    def draw(self) -> None:
        self.delete("all")
        width, y = max(self.winfo_width(), 40), 16
        x = self.position()
        self.create_line(14, y, width - 14, y, fill="#D1D1D6", width=7, capstyle="round")
        self.create_line(14, y, x, y, fill="#007AFF", width=7, capstyle="round")
        self.create_oval(x - 12, y - 10, x + 14, y + 16, fill="#000000", outline="", stipple="gray25")
        self.create_oval(x - 13, y - 13, x + 13, y + 13, fill="#FFFFFF", outline="#E5E5EA", width=1)

    def move(self, event: tk.Event) -> None:
        width = max(self.winfo_width(), 40)
        ratio = min(1.0, max(0.0, (event.x - 14) / (width - 28)))
        value = int(round(self.low + ratio * (self.high - self.low)))
        self.variable.set(value)
        self.command(str(value))
        self.draw()


class HotPackGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.process: subprocess.Popen[str] | None = None
        self.control_file: str | None = None
        self.closing = False
        self.events: queue.Queue[tuple[str, str | int]] = queue.Queue()

        root.title("노트북 손난로")
        root.geometry("500x640")
        root.resizable(False, False)
        root.configure(background="#F2F2F7")
        root.protocol("WM_DELETE_WINDOW", self.close)

        style = ttk.Style(root)
        if "aqua" in style.theme_names():
            style.theme_use("aqua")
        style.configure("App.TFrame", background="#F2F2F7")
        style.configure("Card.TFrame", background="#FFFFFF")
        style.configure("App.TLabel", background="#F2F2F7", foreground="#1C1C1E")
        style.configure("Muted.TLabel", background="#F2F2F7", foreground="#636366")
        style.configure("Card.TLabel", background="#FFFFFF", foreground="#1C1C1E")
        style.configure("Card.TLabelframe", background="#FFFFFF", borderwidth=0, relief="flat")
        style.configure(
            "Card.TLabelframe.Label", background="#F2F2F7", foreground="#636366", font=("Helvetica", 12)
        )

        frame = ttk.Frame(root, padding=(28, 12), style="App.TFrame")
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="🔥  노트북 손난로", font=("Helvetica", 22, "bold"), style="App.TLabel"
        ).pack(pady=(0, 2))
        ttk.Label(
            frame, text="온도에 맞춰 Ollama 부하를 자동 조절합니다.", style="Muted.TLabel"
        ).pack(pady=(0, 9))

        self.cpu_temperature = tk.StringVar(value="--.-°C")
        self.gpu_temperature = tk.StringVar(value="--.-°C")
        self.remaining = tk.StringVar(value="남은 시간 --:--")
        self.state = tk.StringVar(value="대기 중")
        self.cpu_status = tk.StringVar(value="⏸ 대기 중")
        self.gpu_status = tk.StringVar(value="⏸ 대기 중")
        meters = ttk.Frame(frame, style="App.TFrame")
        meters.pack(fill="x")
        cpu_box = ttk.Frame(meters, style="App.TFrame")
        cpu_box.pack(side="left", fill="x", expand=True)
        ttk.Label(cpu_box, text="CPU", style="Muted.TLabel").pack()
        ttk.Label(
            cpu_box, textvariable=self.cpu_temperature, font=("Helvetica", 27, "bold"), style="App.TLabel"
        ).pack()
        self.cpu_status_label = tk.Label(
            cpu_box, textvariable=self.cpu_status, font=("Helvetica", 12, "bold"),
            bg="#F2F2F7", fg="#8E8E93",
        )
        self.cpu_status_label.pack()
        gpu_box = ttk.Frame(meters, style="App.TFrame")
        gpu_box.pack(side="left", fill="x", expand=True)
        ttk.Label(gpu_box, text="GPU", style="Muted.TLabel").pack()
        ttk.Label(
            gpu_box, textvariable=self.gpu_temperature, font=("Helvetica", 27, "bold"), style="App.TLabel"
        ).pack()
        self.gpu_status_label = tk.Label(
            gpu_box, textvariable=self.gpu_status, font=("Helvetica", 12, "bold"),
            bg="#F2F2F7", fg="#8E8E93",
        )
        self.gpu_status_label.pack()
        ttk.Label(frame, textvariable=self.remaining, font=("Helvetica", 13), style="Muted.TLabel").pack(
            pady=(0, 7)
        )

        settings = ttk.LabelFrame(frame, text="설정", padding=(18, 9), style="Card.TLabelframe")
        settings.pack(fill="x")
        self.target = self.slider_row(settings, "목표 온도", 80, 35, 80)
        limit_row = ttk.Frame(settings, style="Card.TFrame")
        limit_row.pack(fill="x", pady=5)
        ttk.Label(limit_row, text="안전 상한", style="Card.TLabel").pack(side="left")
        ttk.Label(
            limit_row, text="85°C · 개별 고정", style="Card.TLabel", font=("Helvetica", 12, "bold")
        ).pack(side="right")
        self.minutes = self.duration_slider(settings, 120)
        self.apply_button = ttk.Button(settings, text="실행 중 온도 설정 적용", command=self.apply_settings)
        self.apply_button.pack(fill="x", pady=(10, 0))

        buttons = ttk.Frame(frame, style="App.TFrame")
        buttons.pack(fill="x", pady=(10, 6))
        self.start_button = IOSButton(buttons, "시작", self.start, "#007AFF")
        self.start_button.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.stop_button = IOSButton(buttons, "중지", self.stop, "#FF3B30")
        self.stop_button.configure(state="disabled")
        self.stop_button.pack(side="left", fill="x", expand=True, padx=(6, 0))

        self.detail = tk.StringVar(value="시작하면 필요한 도구와 모델을 자동으로 준비합니다.")
        ttk.Label(
            frame, textvariable=self.detail, wraplength=430, justify="center", style="Muted.TLabel"
        ).pack(pady=(0, 5))
        ttk.Label(
            frame,
            text="통풍구를 막지 말고 침구 위에서는 사용하지 마세요.",
            foreground="#a33",
            style="App.TLabel",
        ).pack(pady=(2, 0))
        root.after(100, self.poll_events)

    def set_device_status(self, cpu: str, gpu: str) -> None:
        def appearance(status: str) -> tuple[str, str]:
            if status == "가동":
                return "🔥 가열 중", "#FF3B30"
            if status == "정지":
                return "❄️ 냉각 중", "#007AFF"
            if status == "오류":
                return "⚠️ 오류", "#FF9500"
            if status == "종료":
                return "⏹ 종료됨", "#8E8E93"
            return "⏳ 준비 중", "#8E8E93"

        cpu_text, cpu_color = appearance(cpu)
        gpu_text, gpu_color = appearance(gpu)
        self.cpu_status.set(cpu_text)
        self.gpu_status.set(gpu_text)
        self.cpu_status_label.configure(fg=cpu_color)
        self.gpu_status_label.configure(fg=gpu_color)

    def row(self, parent: ttk.Widget, label: str, value: int, low: int, high: int, unit: str) -> tk.IntVar:
        line = ttk.Frame(parent, style="Card.TFrame")
        line.pack(fill="x", pady=5)
        ttk.Label(line, text=label, width=12, style="Card.TLabel").pack(side="left")
        variable = tk.IntVar(value=value)
        ttk.Spinbox(line, from_=low, to=high, textvariable=variable, width=8).pack(side="left")
        ttk.Label(line, text=unit, style="Card.TLabel").pack(side="left", padx=6)
        return variable

    def slider_row(self, parent: ttk.Widget, label: str, value: int, low: int, high: int) -> tk.IntVar:
        block = ttk.Frame(parent, style="Card.TFrame")
        block.pack(fill="x", pady=3)
        heading = ttk.Frame(block, style="Card.TFrame")
        heading.pack(fill="x")
        ttk.Label(heading, text=label, style="Card.TLabel").pack(side="left")
        variable = tk.IntVar(value=value)
        value_label = ttk.Label(heading, text=f"{value}°C", width=6, style="Card.TLabel")
        value_label.pack(side="right")

        def changed(raw: str) -> None:
            rounded = int(round(float(raw)))
            variable.set(rounded)
            value_label.configure(text=f"{rounded}°C")

        IOSSlider(block, variable, low, high, changed).pack(fill="x")
        return variable

    def duration_slider(self, parent: ttk.Widget, value: int) -> tk.IntVar:
        block = ttk.Frame(parent, style="Card.TFrame")
        block.pack(fill="x", pady=3)
        heading = ttk.Frame(block, style="Card.TFrame")
        heading.pack(fill="x")
        ttk.Label(heading, text="실행 시간", style="Card.TLabel").pack(side="left")
        variable = tk.IntVar(value=value)
        value_label = ttk.Label(heading, text=f"{value}분", width=8, style="Card.TLabel")
        value_label.pack(side="right")

        def changed(raw: str) -> None:
            rounded = int(round(float(raw)))
            variable.set(rounded)
            value_label.configure(text="무제한" if rounded == 121 else f"{rounded}분")

        IOSSlider(block, variable, 1, 121, changed).pack(fill="x")
        ttk.Label(block, text="1분", foreground="#777", style="Card.TLabel").pack(side="left")
        ttk.Label(block, text="무제한", foreground="#777", style="Card.TLabel").pack(side="right")
        return variable

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        target, duration_value = self.target.get(), self.minutes.get()
        minutes = 0 if duration_value == 121 else duration_value
        if not 35 <= target <= 80:
            messagebox.showerror("설정 오류", "목표 온도는 35~80°C로 설정하세요.")
            return
        handle, self.control_file = tempfile.mkstemp(prefix="hotpack-", suffix=".json")
        os.close(handle)
        self.write_control(target)
        command = [
            sys.executable, "-u", str(Path(__file__).with_name("hotpack.py")),
            "--target", str(target), "--minutes", str(minutes),
            "--control-file", self.control_file,
        ]
        self.state.set("준비 중")
        self.set_device_status("준비", "준비")
        self.remaining.set("남은 시간: 준비 중")
        self.detail.set("설치와 모델 다운로드가 필요하면 잠시 걸릴 수 있습니다.")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        try:
            self.process = subprocess.Popen(
                command,
                cwd=Path(__file__).parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=0,
                start_new_session=True,
            )
        except OSError as error:
            self.finished(1, str(error))
            return
        threading.Thread(target=self.read_output, daemon=True).start()

    def write_control(self, target: int) -> None:
        if not self.control_file:
            return
        temporary = self.control_file + ".new"
        Path(temporary).write_text(f'{{"target": {target}}}')
        os.replace(temporary, self.control_file)

    def apply_settings(self) -> None:
        if not self.process or self.process.poll() is not None:
            self.detail.set("먼저 손난로를 시작하세요.")
            return
        target = self.target.get()
        if not 35 <= target <= 80:
            messagebox.showerror("설정 오류", "목표 온도는 35~80°C로 설정하세요.")
            return
        self.write_control(target)
        self.detail.set(f"새 목표 적용: 평균 {target}°C · 개별 상한 85°C 고정")

    def read_output(self) -> None:
        assert self.process and self.process.stdout
        buffer = ""
        while character := self.process.stdout.read(1):
            if character in "\r\n":
                if buffer.strip():
                    self.events.put(("line", buffer.strip()))
                buffer = ""
            else:
                buffer += character
        if buffer.strip():
            self.events.put(("line", buffer.strip()))
        self.events.put(("exit", self.process.wait()))

    def handle_line(self, line: str) -> None:
        match = re.search(
            r"CPU (\d+(?:\.\d+)?)°C\s*\|\s*GPU ((?:\d+(?:\.\d+)?)|--)°C\s*\|.*?"
            r"열 압박\s+(\S+)\s*\|\s*CPU\s+(가동|정지)\s*\|\s*GPU\s+(가동|정지)"
            r"\s*\|\s*남은\s+(\S+)", line
        )
        if match:
            self.cpu_temperature.set(f"{float(match.group(1)):.1f}°C")
            self.gpu_temperature.set("--.-°C" if match.group(2) == "--" else f"{float(match.group(2)):.1f}°C")
            cpu_state, gpu_state = match.group(4), match.group(5)
            self.set_device_status(cpu_state, gpu_state)
            if cpu_state == "가동" and gpu_state == "가동":
                self.state.set("CPU · GPU 가열 중")
            elif cpu_state == "가동":
                self.state.set("CPU 가열 · GPU 냉각")
            elif gpu_state == "가동":
                self.state.set("CPU 냉각 · GPU 가열")
            else:
                self.state.set("CPU · GPU 냉각 중")
            self.remaining.set(f"남은 시간 {match.group(6)}")
            self.detail.set(f"시스템 열 압박: {match.group(3)} · 장치별 독립 제어")
        else:
            self.detail.set(line[-120:])

    def poll_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "line":
                    self.handle_line(str(value))
                else:
                    self.finished(int(value))
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def stop(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        self.state.set("종료 중")
        self.set_device_status("준비", "준비")
        try:
            os.killpg(self.process.pid, signal.SIGINT)
        except (OSError, AttributeError):
            self.process.terminate()

    def finished(self, code: int, error: str = "") -> None:
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.state.set("종료됨" if code == 0 else "오류")
        final_status = "종료" if code == 0 else "오류"
        self.set_device_status(final_status, final_status)
        self.remaining.set("남은 시간 --:--")
        if error:
            self.detail.set(error)
        elif code == 0:
            self.detail.set("안전하게 종료하고 모델을 메모리에서 내렸습니다.")
        if self.control_file:
            try:
                Path(self.control_file).unlink()
            except FileNotFoundError:
                pass
            self.control_file = None
        if self.closing:
            self.root.destroy()

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.closing = True
            self.state.set("서버 정리 중")
            self.detail.set("Ollama 부하와 이 프로그램이 시작한 서버를 종료하고 있습니다.")
            self.stop()
        else:
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    HotPackGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
