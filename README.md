# 🔥 hotPack — 노트북을 손난로로

> 로컬 LLM(Ollama)을 돌려서 생기는 열로 노트북을 따뜻하게 유지하는, **온도 조절식 부하 발생기**입니다.
> 온도 센서를 계속 읽으면서 목표 온도에 맞춰 CPU·GPU 부하를 켰다 껐다 합니다.

> [!WARNING]
> 실험용 장난감 프로젝트입니다. **실제 난방기 대용이 아니며**, 침구 위·무릎 담요 위처럼
> 통풍구가 막힌 상태에서 사용하지 마세요. 배터리 수명과 하드웨어 수명에 영향을 줄 수 있습니다.

---

## ✨ 주요 기능

- **CPU / GPU 독립 제어** — 같은 모델을 두 작업으로 나눠 실행합니다.
  CPU 작업은 `num_gpu: 0`, GPU 작업은 `num_gpu: 999`(전 레이어 GPU 오프로드)로 분리해 각각 따로 켜고 끕니다.
- **온도 조절(서모스탯)** — CPU·GPU 평균 온도가 목표에 닿으면 멈추고, 목표보다 3°C 떨어지면 다시 가동합니다.
- **다중 안전장치** — 개별 85°C 하드 상한, macOS 열 압박 신호 감지, 센서 이상 시 즉시 정지.
- **원클릭 실행** — `시작.command` 더블클릭 한 번으로 Homebrew·Python·Ollama·센서·모델까지 자동 설치.
- **GUI** — 슬라이더로 목표 온도·실행 시간을 조절하고, CPU/GPU 온도와 `가열 중`/`냉각 중` 상태를 실시간으로 표시.
- **RAM 기반 모델 자동 선택** — 메모리 여유를 남기도록 모델 크기를 고릅니다.

| RAM | 선택 모델 |
| --- | --- |
| 32 GiB 이상 | `gemma3:12b` |
| 12 GiB 이상 | `gemma3:4b` |
| 그 외 | `gemma3:1b` |

## 🚀 빠른 시작 (macOS)

```bash
git clone https://github.com/1000Pro-1997/Hatpack_Project.git
cd Hatpack_Project
./시작.command
```

또는 Finder에서 **`시작.command`를 더블클릭**하세요.

1. Homebrew, Python, Tk(GUI), Ollama, 온도 센서가 없으면 자동으로 설치합니다.
2. RAM에 맞는 모델을 내려받습니다.
3. 준비가 끝나면 GUI 창이 열리고 터미널은 자동으로 닫힙니다.

첫 실행 때는 인터넷 연결과 macOS 관리자 암호가 필요할 수 있습니다.
GUI 실행에 실패하면 로그(`$TMPDIR/hotpack-gui.log`)의 마지막 부분을 터미널에 보여줍니다.

### GUI 사용법

- **목표 온도 슬라이더** — CPU/GPU 평균 목표 온도 (최대 80°C). 개별 안전 상한 85°C는 고정입니다.
- **실행 시간 슬라이더** — 1~120분, 마지막 칸은 **무제한**. 실행 중 남은 시간이 표시됩니다.
- **실행 중 온도 설정 적용** — 실행 도중 목표 온도를 바꾸면 다음 온도 확인 때부터 반영됩니다.
- 창 닫기 버튼을 누르면 부하·모델·서버 정리가 끝난 뒤 창이 닫힙니다.

## 💻 CLI 사용법

GUI 없이 터미널에서 직접 실행할 수도 있습니다. `Ctrl-C`로 즉시 멈춥니다.

```bash
python3 hotpack.py                                   # 기본값: 목표 80°C, 120분
python3 hotpack.py --target 52 --minutes 20          # 52°C 목표로 20분
python3 hotpack.py --model gemma3:4b --llm-temperature 1.2
python3 hotpack.py --minutes 0                       # 무제한
python3 hotpack.py --no-install                      # 자동 설치 끄기
python3 hotpack.py --sensor-command 'my-temp-tool --cpu'
```

| 옵션 | 기본값 | 범위 | 설명 |
| --- | --- | --- | --- |
| `--target` | `80` | 35–80 | 목표 **기기 온도**(°C, CPU/GPU 평균) |
| `--hysteresis` | `3` | | 다시 가동하기까지의 온도 여유 폭(°C) |
| `--interval` | `3` | 1–30 | 센서 확인 간격(초) |
| `--minutes` | `120` | 0–120 | 자동 종료 시간(분), `0`은 무제한 |
| `--model` | 자동 | | 사용할 Ollama 모델 |
| `--llm-temperature` | `1.0` | 0–2 | LLM 샘플링 temperature |
| `--sensor-command` | | | 숫자 온도 하나를 출력하는 사용자 지정 센서 명령 |
| `--no-install` | | | 누락된 도구 자동 설치 끄기 |

> `--target`은 **기기 온도**이고, `--llm-temperature`는 **모델 출력의 무작위성**입니다.
> 이름은 비슷하지만 발열과는 직접적인 관계가 없습니다.

## 🌡️ 지원 센서

| 환경 | 센서 | 비고 |
| --- | --- | --- |
| Apple Silicon (M1~) | [`macmon`](https://github.com/vladkens/macmon) | CPU·GPU 온도를 모두 읽음 (자동 설치) |
| Apple Silicon (대체) | `smctemp` | 설치되어 있으면 대체 센서로 사용 |
| Intel Mac | `osx-cpu-temp` | CPU 온도 (자동 설치) |
| Linux | `/sys/class/thermal/thermal_zone*/temp` | 자동 감지 |
| 기타 | `--sensor-command` | 숫자 하나를 출력하는 아무 명령 |

## 🛡️ 안전 동작

부하를 만드는 프로그램인 만큼 "멈추는 쪽"을 우선으로 설계했습니다.

- **센서 검증 후 시작** — 센서가 없거나, 읽기에 실패하거나, 10°C 미만의 비정상 값이면 부하를 시작하지 않습니다.
- **평균 목표 도달 시 전체 정지** — CPU·GPU 평균이 목표에 닿으면 두 작업을 모두 멈춥니다.
- **개별 하드 상한 85°C** — CPU나 GPU 중 어느 한쪽이 85°C에 닿으면 해당 작업만 멈춥니다. 이 값은 변경할 수 없습니다.
- **히스테리시스 3°C** — 목표 근처에서 빠르게 ON/OFF를 반복하지 않도록 합니다.
- **macOS 열 압박 감지** — `pmset -g therm`이 `serious`/`critical`을 보고하면 즉시 전부 중지합니다.
- **항상 정리** — 센서가 끊기거나 오류가 나도 `finally`에서 부하를 멈추고 모델을 메모리에서 내립니다.
  Ollama 서버는 **이 프로그램이 직접 띄운 경우에만** 종료합니다.
- **범위 제한** — 목표 온도 최대 80°C, 실행 시간 1~120분 또는 무제한.

## ⚙️ 동작 원리

```text
          ┌──────────── 매 3초 ────────────┐
          ▼                                │
   온도 센서 읽기 (CPU / GPU)               │
          │                                │
   열 압박 serious/critical? ── 예 ──▶ 전체 정지
   평균 ≥ 목표?             ── 예 ──▶ 전체 정지
          │ 아니오                         │
   CPU ≥ 85°C? → CPU 작업 정지             │
   GPU ≥ 85°C? → GPU 작업 정지             │
   평균 ≤ 목표 − 3°C → 해당 작업 가동 ──────┘
```

각 작업(Worker)은 백그라운드 스레드에서 Ollama `/api/generate`에 끝없이 글을 쓰라는 프롬프트를 스트리밍으로 보내
연산 부하를 만들고, 정지 신호를 받으면 요청을 끊습니다.

## 📁 프로젝트 구조

```text
.
├── 시작.command      # macOS 원클릭 실행기 (의존성 설치 → 준비 → GUI 실행)
├── hotpack.py        # 핵심 로직: 설치, 센서, 워커, 온도 제어 루프, CLI
├── gui.py            # Tkinter GUI (hotpack.py를 하위 프로세스로 실행·제어)
└── test_hotpack.py   # 단위 테스트
```

## 🧪 테스트

```bash
python3 -m unittest -v
```

## 📋 요구 사항

- macOS (Apple Silicon 권장) 또는 Linux
- Python 3.10+ (GUI는 Tkinter 필요)
- [Ollama](https://ollama.com) — 없으면 자동 설치
- 모델 다운로드를 위한 디스크 여유 공간 (약 1~8 GB)
