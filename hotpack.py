#!/usr/bin/env python3
"""Thermostatically controlled, deliberately bounded Ollama workload."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API = "http://127.0.0.1:11434"
HARD_MAX_CELSIUS = 85.0
GPU_MAX_OFFLOAD_LAYERS = 999
PROMPT = (
    "Write an endless sequence of short, distinct observations about mathematics, "
    "science, and language. Do not stop early."
)


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def total_memory_gib() -> float:
    system = platform.system()
    try:
        if system == "Darwin":
            return int(run(["sysctl", "-n", "hw.memsize"]).stdout.strip()) / 2**30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (OSError, ValueError, subprocess.SubprocessError):
        return 8.0


def choose_model(memory_gib: float) -> str:
    # Leave generous headroom for the OS and avoid memory-pressure heating.
    if memory_gib >= 32:
        return "gemma3:12b"
    if memory_gib >= 12:
        return "gemma3:4b"
    return "gemma3:1b"


def ensure_ollama(install: bool) -> None:
    if shutil.which("ollama"):
        return
    if not install:
        raise RuntimeError("Ollama가 없습니다. --install 옵션으로 다시 실행하세요.")

    system = platform.system()
    if system == "Darwin" and shutil.which("brew"):
        print("Ollama 설치 중 (Homebrew)…")
        subprocess.run(["brew", "install", "ollama"], check=True)
    elif system == "Linux" and shutil.which("curl"):
        print("Ollama 공식 설치 스크립트를 내려받아 실행합니다…")
        script = urllib.request.urlopen("https://ollama.com/install.sh", timeout=30).read()
        subprocess.run(["sh"], input=script, check=True)
    else:
        raise RuntimeError("자동 설치를 위해 macOS는 Homebrew, Linux는 curl이 필요합니다.")


def api(path: str, payload: dict | None = None, timeout: float = 3) -> bytes:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        API + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def ensure_server() -> subprocess.Popen[str] | None:
    try:
        api("/api/tags")
        return None
    except (OSError, urllib.error.URLError):
        process = subprocess.Popen(
            ["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True
        )
        for _ in range(30):
            if process.poll() is not None:
                raise RuntimeError("Ollama 서버를 시작하지 못했습니다.")
            try:
                api("/api/tags")
                return process
            except (OSError, urllib.error.URLError):
                time.sleep(0.5)
        process.terminate()
        raise RuntimeError("Ollama 서버 시작 시간이 초과되었습니다.")


def ensure_model(model: str) -> None:
    tags = json.loads(api("/api/tags"))
    installed = {item["name"] for item in tags.get("models", [])}
    if model in installed or f"{model}:latest" in installed:
        return
    print(f"모델 다운로드 중: {model}")
    subprocess.run(["ollama", "pull", model], check=True)


class Sensor:
    name = "unknown"

    def read_celsius(self) -> float:
        raise NotImplementedError


class CommandSensor(Sensor):
    def __init__(self, command: list[str], name: str):
        self.command, self.name = command, name

    def read_celsius(self) -> float:
        result = run(self.command, check=False)
        if result.returncode:
            raise RuntimeError(f"{self.name} 센서 읽기 실패: {result.stderr.strip()}")
        output = result.stdout
        match = re.search(r"(-?\d+(?:\.\d+)?)", output)
        if not match:
            raise RuntimeError(f"{self.name} 출력에서 온도를 찾지 못했습니다: {output!r}")
        temperature = float(match.group(1))
        if not 10 <= temperature <= 110:
            raise RuntimeError(f"{self.name}가 비정상 온도 {temperature:.1f}°C를 반환했습니다")
        return temperature


class LinuxSensor(Sensor):
    name = "Linux thermal zone"

    def __init__(self, path: Path):
        self.path = path

    def read_celsius(self) -> float:
        value = float(self.path.read_text().strip())
        temperature = value / 1000 if value > 200 else value
        if not 10 <= temperature <= 110:
            raise RuntimeError(f"Linux 센서가 비정상 온도 {temperature:.1f}°C를 반환했습니다")
        return temperature


class MacmonSensor(Sensor):
    name = "macmon (CPU/GPU 평균 제어, 개별 85°C 안전 감시)"

    def __init__(self):
        self.cpu_celsius: float | None = None
        self.gpu_celsius: float | None = None
        self.safety_celsius: float | None = None

    @staticmethod
    def parse_temperatures(output: str) -> tuple[float | None, float | None]:
        try:
            data = json.loads(output.strip().splitlines()[-1])
            temperatures = data["temp"]
            readings = [
                float(temperatures.get("cpu_temp_avg", 0)),
                float(temperatures.get("gpu_temp_avg", 0)),
            ]
        except (ValueError, KeyError, IndexError, json.JSONDecodeError) as error:
            raise RuntimeError(f"macmon 온도 출력을 해석하지 못했습니다: {error}") from error
        cpu = readings[0] if 10 <= readings[0] <= 110 else None
        gpu = readings[1] if 10 <= readings[1] <= 110 else None
        valid = [value for value in (cpu, gpu) if value is not None]
        if not valid:
            raise RuntimeError(f"macmon이 비정상 온도를 반환했습니다: {readings}")
        return cpu, gpu

    @staticmethod
    def parse(output: str) -> float:
        cpu, gpu = MacmonSensor.parse_temperatures(output)
        valid = [value for value in (cpu, gpu) if value is not None]
        return sum(valid) / len(valid)

    def read_celsius(self) -> float:
        try:
            result = subprocess.run(
                ["macmon", "pipe", "--samples", "1", "--interval", "300"],
                check=False,
                text=True,
                capture_output=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("macmon 센서 응답 시간이 초과되었습니다") from error
        if result.returncode:
            raise RuntimeError(f"macmon 센서 읽기 실패: {result.stderr.strip()}")
        self.cpu_celsius, self.gpu_celsius = self.parse_temperatures(result.stdout)
        valid = [value for value in (self.cpu_celsius, self.gpu_celsius) if value is not None]
        self.safety_celsius = max(valid)
        return sum(valid) / len(valid)


def ensure_sensor(install: bool) -> None:
    if platform.system() != "Darwin":
        return
    # Old SMC tools can print a misleading 0.0°C after an IOKit error on newer
    # Apple Silicon. macmon reads CPU and GPU temperatures via IOReport instead.
    if platform.machine() == "arm64" and not shutil.which("macmon") and install and shutil.which("brew"):
        print("Apple Silicon 온도 센서 설치 중 (macmon)…")
        subprocess.run(["brew", "install", "macmon"], check=True)
    elif platform.machine() != "arm64" and not shutil.which("osx-cpu-temp") and install and shutil.which("brew"):
        print("Intel Mac 온도 센서 설치 중 (osx-cpu-temp)…")
        subprocess.run(["brew", "install", "osx-cpu-temp"], check=True)


def find_sensor(custom_command: str | None = None) -> Sensor:
    if custom_command:
        # Intentionally no shell: split a simple trusted command into argv.
        import shlex

        return CommandSensor(shlex.split(custom_command), "사용자 지정 센서")
    if platform.system() == "Darwin":
        errors: list[str] = []
        if platform.machine() == "arm64" and shutil.which("macmon"):
            sensor = MacmonSensor()
            try:
                sensor.read_celsius()  # Verify before any Ollama load starts.
                return sensor
            except RuntimeError as error:
                errors.append(str(error))
        if platform.machine() == "arm64" and shutil.which("smctemp"):
            sensor = CommandSensor(["smctemp", "-c", "-i25", "-n180", "-f"], "smctemp")
            try:
                sensor.read_celsius()
                return sensor
            except RuntimeError as error:
                errors.append(str(error))
        if platform.machine() != "arm64" and shutil.which("osx-cpu-temp"):
            sensor = CommandSensor(["osx-cpu-temp", "-c"], "osx-cpu-temp")
            sensor.read_celsius()
            return sensor
        if errors:
            raise RuntimeError("온도 센서 검증 실패 — " + " / ".join(errors))
    if platform.system() == "Linux":
        candidates = sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
        if candidates:
            return LinuxSensor(candidates[0])
    raise RuntimeError(
        "정상 작동하는 섭씨 온도 센서를 찾지 못했습니다. Apple Silicon에서는 macmon이 필요합니다. "
        "--sensor-command를 지정하세요."
    )


def mac_thermal_pressure() -> str:
    if platform.system() != "Darwin":
        return "nominal"
    result = run(["pmset", "-g", "therm"], check=False)
    text = (result.stdout + result.stderr).lower()
    for level in ("critical", "serious", "fair", "nominal"):
        if level in text:
            return level
    return "unknown"


class Worker:
    def __init__(self, model: str, llm_temperature: float, num_gpu: int):
        self.model = model
        self.llm_temperature = llm_temperature
        self.num_gpu = num_gpu
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self) -> None:
        if self.active:
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def _work(self) -> None:
        while not self.stop_event.is_set():
            payload = json.dumps(self.request_payload()).encode()
            request = urllib.request.Request(
                API + "/api/generate", data=payload, headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    while not self.stop_event.is_set() and response.readline():
                        pass
            except (OSError, urllib.error.URLError):
                if not self.stop_event.wait(1):
                    continue

    def request_payload(self) -> dict:
        return {
                "model": self.model,
                "prompt": PROMPT,
                "stream": True,
                "keep_alive": "2m",
                "options": {
                    "temperature": self.llm_temperature,
                    "num_predict": 512,
                    "num_gpu": self.num_gpu,
                },
            }


@dataclass
class Config:
    target: float
    maximum: float
    hysteresis: float
    interval: float
    duration: float


def format_remaining(seconds: float) -> str:
    if math.isinf(seconds):
        return "무제한"
    seconds = max(0, int(math.ceil(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def refresh_config(config: Config, control_file: str | None) -> None:
    if not control_file:
        return
    try:
        values = json.loads(Path(control_file).read_text())
        target = float(values["target"])
        if 35 <= target <= 80:
            config.target = target
            config.maximum = HARD_MAX_CELSIUS
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        pass  # Keep the last known-safe settings during an atomic GUI update.


def control(
    cpu_worker: Worker,
    gpu_worker: Worker,
    sensor: Sensor,
    config: Config,
    control_file: str | None = None,
) -> None:
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    deadline = math.inf if config.duration == 0 else time.monotonic() + config.duration * 60
    print(f"센서: {sensor.name} | 목표 {config.target:.1f}°C | 절대 상한 {config.maximum:.1f}°C")
    try:
        while not stopping and time.monotonic() < deadline:
            refresh_config(config, control_file)
            temperature = sensor.read_celsius()
            pressure = mac_thermal_pressure()
            cpu = getattr(sensor, "cpu_celsius", None)
            gpu = getattr(sensor, "gpu_celsius", None)
            cpu_control = cpu if cpu is not None else temperature
            gpu_control = gpu
            cpu_text = f"{cpu:.1f}" if cpu is not None else f"{temperature:.1f}"
            gpu_text = f"{gpu:.1f}" if gpu is not None else "--"

            thermal_emergency = pressure in {"serious", "critical"}
            average_hot = temperature >= config.target
            cpu_hot = cpu_control >= config.maximum
            gpu_hot = gpu_control is not None and gpu_control >= config.maximum

            if thermal_emergency or average_hot:
                cpu_worker.stop()
                gpu_worker.stop()
            else:
                if cpu_hot:
                    cpu_worker.stop()
                elif temperature <= config.target - config.hysteresis and cpu_control <= config.maximum - config.hysteresis:
                    cpu_worker.start()

                if gpu_control is None or gpu_hot:
                    gpu_worker.stop()
                elif temperature <= config.target - config.hysteresis and gpu_control <= config.maximum - config.hysteresis:
                    gpu_worker.start()

            cpu_state = "가동" if cpu_worker.active else "정지"
            gpu_state = "가동" if gpu_worker.active else "정지"
            print(
                f"\rCPU {cpu_text}°C | GPU {gpu_text}°C | 기준 평균 {temperature:.1f}°C | "
                f"열 압박 {pressure:8s} | CPU {cpu_state} | GPU {gpu_state} | "
                f"남은 {format_remaining(deadline - time.monotonic())}  ",
                end="", flush=True
            )
            if thermal_emergency:
                print("\n시스템 열 압박 신호로 CPU/GPU 작업을 모두 중지합니다.")
            time.sleep(config.interval)
    finally:
        cpu_worker.stop()
        gpu_worker.stop()
        try:
            api("/api/generate", {"model": cpu_worker.model, "keep_alive": 0}, timeout=5)
        except (OSError, urllib.error.URLError):
            pass
    print("\n종료했습니다. 모델을 메모리에서 내렸습니다.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="온도에 따라 Ollama 부하를 조절하는 노트북 손난로")
    parser.add_argument("--target", type=float, default=80, help="목표 온도 °C (기본 80, 최대 80)")
    parser.add_argument("--hysteresis", type=float, default=3, help="ON/OFF 온도 여유 폭")
    parser.add_argument("--interval", type=float, default=3, help="센서 확인 간격(초)")
    parser.add_argument("--minutes", type=float, default=120, help="자동 종료 시간(분, 기본 120, 0은 무제한)")
    parser.add_argument("--model", help="사용할 Ollama 모델(생략 시 RAM에 맞게 선택)")
    parser.add_argument("--llm-temperature", type=float, default=1.0, help="LLM 샘플링 temperature")
    parser.add_argument("--sensor-command", help="숫자 온도를 출력하는 센서 명령")
    parser.add_argument("--control-file", help=argparse.SUPPRESS)
    parser.add_argument("--prepare-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--no-install", action="store_false", dest="install", help="필요한 도구 자동 설치를 끔"
    )
    parser.set_defaults(install=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 35 <= args.target <= 80:
        raise SystemExit("--target은 35~80°C만 허용합니다.")
    if not 0 <= args.llm_temperature <= 2:
        raise SystemExit("--llm-temperature는 0~2만 허용합니다.")
    if not 0 <= args.minutes <= 120 or not 1 <= args.interval <= 30:
        raise SystemExit("시간은 0(무제한)~120분, 확인 간격은 1~30초 범위여야 합니다.")

    ensure_ollama(args.install)
    ensure_sensor(args.install)
    sensor = find_sensor(args.sensor_command)  # Verify safety sensor before creating load.
    model = args.model or choose_model(total_memory_gib())
    print(f"선택 모델: {model} (RAM {total_memory_gib():.1f} GiB)")
    server = ensure_server()
    try:
        ensure_model(model)
        if args.prepare_only:
            print("준비 완료: GUI를 시작할 수 있습니다.")
            return 0
        control(
            Worker(model, args.llm_temperature, 0),
            Worker(model, args.llm_temperature, GPU_MAX_OFFLOAD_LAYERS),
            sensor,
            Config(args.target, HARD_MAX_CELSIUS, args.hysteresis, args.interval, args.minutes),
            args.control_file,
        )
    finally:
        if server is not None:
            server.terminate()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.SubprocessError, urllib.error.URLError) as error:
        print(f"오류: {error}", file=sys.stderr)
        raise SystemExit(1)
