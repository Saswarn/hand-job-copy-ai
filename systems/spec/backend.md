# Spec: Backend (Python service)

สถานะ: Draft · ที่มา: design session 2026-10-29

## Stack

- Python 3.11.15, FastAPI + uvicorn, จัดการด้วย **uv** อยู่ใน `backend/`
- โครง: `app/{api,core,schemas,services,utils}`, ไฟล์โมเดลอยู่ใน `models/`
- Backend เป็นเจ้าของกล้องและ GPIO **คนเดียว**
- Pi 5 ใช้ `RPi.GPIO` ไม่ได้ → ใช้ `gpiozero` (+ `lgpio`); dev บน Windows ใช้ `GPIOZERO_PIN_FACTORY=mock`

## Architecture

```
┌──────────── backend (Docker, restart: always) ──────────────────────┐
│ linuxpy (v4l2) ── raw MJPEG ──► เฟรมล่าสุด ──┬──► stream thread           │
│                                              │    scaled decode → crop    │
│                                              │    → วาดกรอบผล AI ล่าสุด     │
│                                              │    → encode ──► /ws binary │
│                                              └──► AI (5–10 fps)            │
│                                                   decode → crop → binary  │
│                                                   → letterbox 640         │
│                                                   → onnxruntime → กรอบ    │
│ gpiozero ◄── คำสั่ง UV                                                     │
└─────────────────────────────────────────────────────────────────────┘
            ▲ HTTP / WebSocket :8000
┌──────── frontend (Electron) ────────┐
└─────────────────────────────────────┘
```

## Camera pipeline

1. ดึง JPEG เฟรมดิบจากกล้อง (Arducam IMX585 USB3, MJPEG) ด้วย `linuxpy` — ไม่ผ่าน OpenCV ในขั้นนี้
2. **ภาพแสดงผล:** ทุกเฟรมที่ส่งไปจอ: decode แบบย่อขนาด (`simplejpeg`, 1/1 · 1/2 · 1/4 เลือกขนาดเล็กสุดที่ยังกว้าง ≥ `stream.min_width` หลัง crop) → crop ตาม `camera.crop` → วาดกรอบของผล AI ล่าสุด (numpy, ไม่ใช้ OpenCV) → encode ด้วย `stream.quality` · ทำใน thread ของตัวเอง ใช้เฟรมล่าสุดเสมอ ข้ามเฟรมที่ทำไม่ทัน
3. **ภาพเข้า AI:** decode เฉพาะเฟรมที่จะ infer ด้วย `simplejpeg` (libjpeg-turbo) แบบย่อขนาดตอน decode (1/2, 1/4)
4. ล็อกค่ากล้องเอง: exposure, gain, white balance (ห้าม auto ใต้ UV)
5. **ซูม** (ตั้งครั้งเดียวตอนติดตั้ง): ถ้ากล้องรองรับ `zoom_absolute` → ให้กล้องครอปเอง ไม่งั้นครอปใน Python ตาม `camera.crop` (ใช้ทั้งภาพจอและภาพเข้า AI)

ทำไม decode / encode ทุกเฟรม: ให้ภาพที่จอเห็นตรงกับภาพที่ AI เห็น (crop เดียวกัน) และกรอบติดมากับภาพ frontend ไม่ต้องหน่วงภาพ / แปลงพิกัด
ราคาที่จ่าย: Pi 5 ไม่มี hardware JPEG decoder → CPU สูงขึ้น (เสี่ยง BE-2, BE-3) · ถ้าไม่พอ ลด `stream.min_width` (decode 1/2) หรือ `stream.quality`, หรือลดความละเอียดที่ดึงจากกล้อง
ข้อแลกเปลี่ยน: ผล AI มา 5–10 ครั้ง/วินาที กรอบจึงตามหลังมือที่ขยับ ~100–200ms (ไม่หน่วงภาพเพื่อให้ภาพไม่ช้า)

แหล่งภาพ: env `CAMERA_SOURCE=fake` → ภาพทดสอบที่สร้างเอง หรือวน `*.jpg` จากโฟลเดอร์ใน `CAMERA_FAKE_DIR` (Windows, Docker dev, เทสต์) · `CAMERA_SOURCE=csi` → กล้องสายแพหมายเลข 0 ผ่าน `rpicam-vid` บน Raspberry Pi OS โดยใช้ความละเอียดและ fps จาก `camera.capture` · ค่าอื่น / ไม่ตั้ง → `linuxpy` จาก `camera.device` ถ้าเปิดกล้องไม่ได้ backend ไม่ start (ไม่สลับไปภาพปลอมเงียบๆ)

สำหรับกล้อง CSI ให้ปิด `rpicam-hello` ก่อน แล้วรัน `CAMERA_SOURCE=csi bash systems/scripts/run-backend.sh` จากโฟลเดอร์โปรเจกต์บน Pi โดยตรง สคริปต์นี้ต้องเรียก `rpicam-vid` ได้จาก `PATH` ส่วน Docker ของ Pi ในโปรเจกต์ตั้งค่าสำหรับกล้อง USB

#### ภาพสีสำหรับ HandCleanEngine บน CSI

เมื่อตั้ง `HANDCLEAN_ENGINE=real` หรือ `mock` ระบบจะรับ `CAMERA_SOURCE=csi` เป็นชื่อเดียวกับ `csi-rgb` และอ่านภาพสีผ่าน Picamera2 หากไม่ได้เปิด engine ค่า `csi` จะยังใช้ `rpicam-vid` ตามเดิม

โหมด `CAMERA_SOURCE=csi-rgb` ใช้ Picamera2 บน Raspberry Pi OS โดย Python ที่ใช้รัน backend ต้อง import `picamera2` จากแพ็กเกจระบบได้ ใช้ interpreter เวอร์ชันเดียวกับแพ็กเกจของระบบและเปิดให้ environment เห็น system packages โหมดนี้ใช้กล้อง CSI เพียง instance เดียว ภาพต้นฉบับ RGB `uint8` เข้าทาง `Camera.latest_color` และแปลงเป็น JPEG เพื่อส่งผ่านเส้นทางภาพสดเดิม

- ตั้ง `camera.capture` ให้ตรงความละเอียดภาพนิ่งและ FPS ที่กล้องรองรับ `ColorFrame.preview()` ย่อภาพให้ด้านยาวไม่เกิน 640 px พร้อมคง `frame_id` และ `timestamp_ms` จาก `SensorTimestamp` ของภาพเดียวกัน
- ระบุ `camera.csi_rgb` ใน `config.json` โดยใช้ `exposure_us`, `analogue_gain` และ `colour_gains` คู่แดง/น้ำเงิน ค่าทั้งหมดต้องมากกว่าศูนย์ และ exposure ต้องไม่เกินเวลาต่อเฟรม โหมดนี้ส่งค่าคงที่และปิด AE/AWB ตอนตั้งกล้อง
- รอบตรวจรับบน Pi ต้องยืนยันค่าที่กล้องใช้จริงจาก metadata และยืนยันว่ารุ่นกล้องรองรับความละเอียดและ FPS ที่เลือก เส้นทาง USB ยังใช้ MJPEG และยังไม่ใช้เป็นภาพสำหรับ engine

## Scan logic

- กฎตัดสินผลอยู่ที่ `app/services/scan.judge` · รับกรอบของทุกเฟรมเป็น `Box` ตามลำดับ
- สแกนนาน `scan_seconds` (default 5)
- เจอ `dirt` ที่ confidence ≥ `dirt_conf` (default 0.5) ติดต่อกัน ≥ `dirt_min_frames` เฟรม (default 3) → "ยังมีคราบ" ไม่งั้น → "สะอาด"
- `/ws` รัน scan เป็น async task แยกจากลูปรับข้อความ ใช้ Detector ตัวเดียวกับ Stream ผ่าน `app.state.detector` · ลำดับข้อความและข้อจำกัด polling ดู [API §ลำดับข้อความสแกน](../api.md#ลำดับข้อความสแกน-han-17)
- ไม่บันทึกผล / รูป / ประวัติ

## Config

ไฟล์เดียว [`backend/config/config.json`](../../backend/config/config.json) โหลดเป็น Pydantic model `AppConfig` ใน `app/core/config.py` · key ที่ไม่รู้จัก, ค่าเกินช่วง หรือ crop ว่าง → backend ไม่ start (บอก key ที่ผิดใน error) · อ่านผ่าน `GET /config` ได้แล้ว · `PUT /config`   บันทึกที่ตั้งค่าสำเร็จแล้วตอบ 204 ค่ากล้อง ภาพสด และขาควบคุมไฟ UV จะเริ่มใช้หลังปิดแล้วเปิด backend ใหม่ (`app/api/config.py`)

| key | ความหมาย |
|---|---|
| `uv_gpio` | เลขขา GPIO ของไฟ UV 3 ช่องพอดี (แก้ตามสายจริงได้โดยไม่ต้องแก้โค้ด) |
| `camera.*` | device, ความละเอียดที่ดึง, exposure, gain, white balance, zoom, crop · **exposure / gain / white_balance / zoom ยังไม่ถูกส่งไปที่กล้อง** (รอผล `--list-ctrls` ใน Open items) |
| `camera.crop` | `[x1, y1, x2, y2]` สัดส่วน 0–1 ของภาพกล้อง ใช้ทั้งภาพจอและภาพเข้า AI |
| `stream.min_width` | ความกว้างขั้นต่ำของภาพที่ส่งไปจอ (px) · ภาพจริงกว้าง min_width ถึง < 2× |
| `stream.quality` | JPEG quality ของภาพที่ส่งไปจอ |
| `binary_threshold` | threshold ของภาพ binary ก่อนเข้า AI |
| `scan_seconds`, `dirt_conf`, `dirt_min_frames` | เกณฑ์การสแกน |

## Safety (ไฟ UV)

ไฟ UV ดับเองทุกครั้งเมื่อ:
- สแกนเสร็จ
- backend ปิดหรือพัง
- frontend หลุดการเชื่อมต่อ

## Deployment

- Docker image arm64 ตรงกับ Raspberry Pi OS Bookworm: `python:3.11.15-slim-bookworm` + uv `0.11.8`
- Dev (Windows): `docker-compose.yml` (QEMU, mock GPIO, ภาพปลอม `CAMERA_SOURCE=fake`)

### ตรวจการติดตั้ง HandCleanEngine

ใช้ Linux 64 บิต และ Python 3.11 พร้อม `venv` และ Git หาก OpenCV แจ้งว่าหา `libGL.so.1` ไม่พบ ให้ติดตั้งแพ็กเกจระบบ `libgl1` บน Raspberry Pi OS

```bash
bash systems/scripts/setup-engine.sh
```

สคริปต์ติดตั้ง engine จาก Git revision ที่กำหนดและ dependencies ที่ pin ไว้ใน `backend/engine-check/requirements.txt` ลงใน `backend/engine-check/.venv` แยกจาก backend เพราะ engine ต้องใช้ NumPy 1.x ส่วน backend ใช้ NumPy 2.x หาก Python 3.11 อยู่ที่ path อื่น ให้ระบุ `PYTHON_BIN=/path/to/python3.11` ตอนรัน

สคริปต์รัน `pip check` และชุดเทสต์ upstream ซึ่งรวมการส่งภาพดำสังเคราะห์ให้ engine จริงและตรวจสถานะเมื่อไม่มีมือ ใช้คำสั่งเดียวกันบน Pi 5 เพื่อยืนยันการติดตั้งบน arm64 ชุดนี้ทดสอบเฉพาะ engine ด้วยภาพสังเคราะห์และไม่เปิดกล้องหรือ UV

### เปิด engine scan ผ่าน WebSocket

หลังติดตั้ง engine แล้ว สามารถลองครบสองด้านด้วย MockEngine ต้นทางและกล้องจำลองบนเครื่องพัฒนาได้

```bash
CAMERA_SOURCE=fake-rgb HANDCLEAN_ENGINE=mock GPIOZERO_PIN_FACTORY=mock bash systems/scripts/run-backend.sh
```

บน Pi ให้ใช้ `CAMERA_SOURCE=csi-rgb HANDCLEAN_ENGINE=real` แล้วตั้ง `camera.csi_rgb` ตามหัวข้อภาพสีข้างต้น ทดสอบด้วย GPIO mock ก่อน และใช้ `GPIOZERO_PIN_FACTORY=lgpio` เมื่อตรวจอุปกรณ์จริง ส่ง `{"type":"engine_scan_start"}` ผ่าน `ws://localhost:8000/ws` และอ่านผลตาม [WebSocket API](../api.md)

Backend เรียก process จาก `backend/engine-check/.venv` หนึ่งตัวต่อหนึ่ง scan session และส่งภาพ RGB โดยตรงผ่าน pipe สัญญา deadline ของทั้งรอบใช้ `engine_scan_seconds` ค่าเริ่มต้น 30 วินาที ส่วนการสื่อสารหนึ่งคำสั่งตัดที่ 15 วินาทีเพื่อให้ process ที่ค้างถูกเก็บกวาด การจับภาพเต็มความละเอียดและการเปิด engine บน Pi ยังต้องตรวจว่าสำเร็จภายในเวลาที่ตั้งไว้

## Type และเทสต์

- `uv run pyright` — strict ทั้ง `app/` และ `tests/` ต้อง 0 error · stub ของ lib ที่ไม่มี type (`gpiozero`, `simplejpeg`, `linuxpy`) อยู่ใน `backend/typings/` เขียนเฉพาะที่ใช้ จะใช้ API ใหม่ของ lib พวกนี้ต้องเพิ่มใน stub ก่อน
- `uv run pytest` — `tests/conftest.py` ตั้ง mock GPIO + ภาพปลอมให้ทุกเทสต์ · ครอบ: `/ws` (ภาพ, `uv_set`, error, หลุดแล้วดับไฟ), ปิด backend แล้วดับไฟ, config ผิดแล้ว fail, `Latest`, `UvService`, ภาพปลอม, crop / วาดกรอบ
- Pi: `docker-compose.yml` + `docker-compose.pi.yml` (ส่ง `/dev/video0`, `/dev/gpiochip0` เข้า container, `restart: always`)

## Open items

- [ ] **Deployment บน Pi:** spec เดิมกำหนด systemd; ตอนนี้ setup เป็น Docker `restart: always` — ยืนยันว่าใช้ Docker
- [ ] **ผล `v4l2-ctl -d /dev/video0 --list-ctrls`** บน Pi จริง (มี `zoom_absolute` ไหม, ชื่อ control ของ exposure / gain / WB)
- [ ] **ความละเอียดที่ดึงจากกล้อง** (4K / 1080p / 720p) — ตัดสินหลัง benchmark
- [ ] **เพดานเวลาเปิดไฟ UV** (เสนอ 60 วินาที แม้เปิดจากหน้า Debug) — ยังไม่ยืนยัน
- [ ] ชื่อ device GPIO บน Pi 5 (`/dev/gpiochip0` หรือ `gpiochip4` ขึ้นกับ kernel)
