# HandCleanStation

เครื่อง kiosk ตรวจหาคราบสกปรกบนมือ: วางมือใต้ไฟ UV-A → กล้องถ่าย realtime → AI (YOLO11n, ภาพ binary) หา `hand` / `dirt` → แสดงผล "สะอาด" / "ยังมีคราบ"

Jira: [HAN](https://phuminforwork.atlassian.net/browse/HAN) · ทุกอย่างที่ต้องยึดอยู่ใน [systems/](systems/)

## ภาพรวม

```
┌──────────────────────── Raspberry Pi 5 ────────────────────────┐
│                                                                 │
│  frontend/ (Electron + React + TS)                              │
│  หน้าจอ kiosk 1366×768: วิดีโอสอน · AI detection · Settings      │
│            │  HTTP + WebSocket  :8000                           │
│            ▼                                                    │
│  backend/ (FastAPI, Docker arm64)                               │
│  กล้อง (linuxpy) · AI (onnxruntime) · ไฟ UV (gpiozero)           │
│      │ USB3                    │ GPIO → รีเลย์                   │
│      ▼                         ▼                                │
│  Arducam IMX585            ไฟ UV × 3                             │
└─────────────────────────────────────────────────────────────────┘
```

| ส่วน | โฟลเดอร์ | Stack | Spec |
|---|---|---|---|
| Frontend | [frontend/](frontend/) | Electron 44 + React 19 + TS (electron-vite) | [systems/spec/frontend.md](systems/spec/frontend.md) |
| Backend | [backend/](backend/) | Python 3.11.15 + FastAPI, uv | [systems/spec/backend.md](systems/spec/backend.md) |
| AI | [backend/models/](backend/models/) | YOLO11n `.onnx` + onnxruntime | [systems/spec/ai.md](systems/spec/ai.md) |
| Hardware | — | Pi 5 16GB, IMX585, TD1630-3, UV × 3 | [systems/hardware.md](systems/hardware.md) |

## Ports

| Port | ใคร | ใช้ทำอะไร |
|---|---|---|
| `8000` | backend | REST (`/health`, …) + WebSocket `/ws` — สัญญาอยู่ใน [systems/api.md](systems/api.md) |
| `5173` | frontend (dev เท่านั้น) | Vite dev server ของ renderer |

## เริ่มต้น

สำหรับติดตั้ง AI และทดลองสแกนด้วยกล้องจำลองหรือกล้อง CSI บน Pi 5 ดู [วิธีใช้งาน HandCleanEngine](HANDCLEAN_ENGINE.md)

ต้องมี: Node 22+ / npm, [uv](https://docs.astral.sh/uv/), Docker

| | Windows (dev) | Pi |
|---|---|---|
| Backend | `systems\scripts\setup-backend.ps1` → `run-backend.ps1` | `systems/scripts/setup-backend.sh` → `run-backend.sh` |
| Frontend | `systems\scripts\setup-frontend.ps1` → `run-frontend.ps1` | `setup-frontend.sh` → `KIOSK=1 run-frontend.sh` |
| Docker | `setup-docker.ps1` → `run-docker.ps1` (QEMU, mock GPIO) | `setup-docker.sh` → `run-docker.sh` (กล้อง + GPIO จริง) |

ตรวจ: `curl http://localhost:8000/health` → `{"status":"ok"}`

ก่อนเปิด PR:

| | Type | Lint | Test |
|---|---|---|---|
| Backend (`backend/`) | `uv run pyright` | — | `uv run pytest` |
| Frontend (`frontend/`) | `npm run typecheck` | `npm run lint` | `npm test` |

## กติกาของทีม

- **Version ตายตัวเท่านั้น** ห้าม `^ ~ >= <= < >` ใน `frontend/package.json` และ `backend/pyproject.toml` (frontend มี `.npmrc save-exact=true`, backend ใช้ `uv add --bounds exact`)
- แก้พฤติกรรม → อัปเดต `systems/` ใน PR เดียวกัน · กระทบ metric → วัดและบันทึกใน [systems/metrics/](systems/metrics/)
- ตัวเลขความเร็ววัดบน Pi จริงเท่านั้น
- Type safe ทั้งโปรเจกต์ (pyright strict / TS strict) และ unit test ทุก logic ที่มี branch หรือแตะความปลอดภัย
- เริ่มงานที่มี ticket และเปิด PR ด้วย skill `ticket-flow` · ดูภาพรวมด้วย skill `catalog-creator` (ดู [AGENTS.md](AGENTS.md))
