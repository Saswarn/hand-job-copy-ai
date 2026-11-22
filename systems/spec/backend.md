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

- ตั้งกล้องแบบเดียวกับ `handclean-pi/pi/handclean_pi.py` ทาง `camera.csi_rgb` (ไม่ใส่ก็ได้ ทุกค่าเป็นอัตโนมัติ) ส่วนทางนี้ใช้ `camera.capture` แค่ `fps` (`width` / `height` เป็นของทาง JPEG/V4L2)

  | key | ไม่ใส่ | ใส่ |
  |---|---|---|
  | `size` `[w, h]` | กว้าง 1920 สัดส่วนตามเซนเซอร์ ความสูงปัดเป็นทวีคูณ 16 → HQ IMX477 (4056×3040) ได้ 1920×1440 มุมเต็ม (KBON เลือก 2026-11-22) | ขนาดภาพที่ขอจาก Picamera2 |
  | `sensor_mode` `[w, h]` | libcamera เลือกเอง | บังคับโหมดเซนเซอร์ เช่น `[2028, 1520]` = HQ binned มุมเต็ม |
  | `exposure_us` | AE ทำงาน | ปิด AE ล็อก exposure (ต้องไม่เกินเวลาต่อเฟรม `1e6 / capture.fps` µs) |
  | `analogue_gain` | 1.0 เมื่อล็อก exposure | ใช้ได้เฉพาะคู่กับ `exposure_us` |
  | `colour_gains` `[r, b]` | AWB ทำงาน | ปิด AWB ล็อก gain แดง/น้ำเงิน |

  ในกล่อง UV ควรล็อกทั้ง exposure และ white balance เพื่อให้สีคงที่ทุกรอบ (ค่าต้องหาจากชุดไฟจริง) · backend log ขนาดภาพ, โหมดเซนเซอร์ และค่าที่ล็อกตอนเปิดกล้อง
- `ColorFrame.preview()` ย่อภาพให้ด้านยาวไม่เกิน 640 px พร้อมคง `frame_id` และ `timestamp_ms` จาก `SensorTimestamp` ของภาพเดียวกัน
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

ใช้ Linux 64 บิต และ Python 3.11 พร้อม `venv`, Git และ `g++` ที่มี OpenMP (`build-essential`) หาก OpenCV แจ้งว่าหา `libGL.so.1` ไม่พบ ให้ติดตั้งแพ็กเกจระบบ `libgl1` บน Raspberry Pi OS

```bash
bash systems/scripts/setup-engine.sh
```

สคริปต์ติดตั้ง engine จาก Git revision ที่กำหนดและ dependencies ที่ pin ไว้ใน `backend/engine-check/requirements.txt` ลงใน `backend/engine-check/.venv` แยกจาก backend เพราะ engine ต้องใช้ NumPy 1.x ส่วน backend ใช้ NumPy 2.x หาก Python 3.11 อยู่ที่ path อื่น ให้ระบุ `PYTHON_BIN=/path/to/python3.11` ตอนรัน

สคริปต์ build GrabCut แบบ C++ (`native/grabcut/build.sh` ของ engine ใช้ `-march=native` จึงต้องรันบนเครื่องที่จะใช้งานจริง) ไว้ที่ `backend/engine-check/lib/libhcgrabcut.so` (ไม่เก็บใน git) และ [`engine.toml`](../../backend/engine-check/engine.toml) ตั้ง `mask.method = "grabcut_cpp"` ถ้าหาไฟล์ไม่เจอ engine จะไม่ start แทนที่จะช้าลงราว 4 เท่าแบบเงียบ ๆ (laptop: 291 ms เทียบกับ 2,042 ms ตามที่ handclean-pi วัดไว้) สคริปต์ตรวจด้วยว่า worker รายงาน `mask_backend = grabcut_cpp`

สคริปต์รัน `pip check` และชุดเทสต์ upstream ซึ่งรวมการส่งภาพดำสังเคราะห์ให้ engine จริงและตรวจสถานะเมื่อไม่มีมือ ใช้คำสั่งเดียวกันบน Pi 5 เพื่อยืนยันการติดตั้งบน arm64 ชุดนี้ทดสอบเฉพาะ engine ด้วยภาพสังเคราะห์และไม่เปิดกล้องหรือ UV

### เปิด engine scan ผ่าน WebSocket

หลังติดตั้ง engine แล้ว สามารถลองครบสองด้านด้วย MockEngine ต้นทางและกล้องจำลองบนเครื่องพัฒนาได้

```bash
CAMERA_SOURCE=fake-rgb HANDCLEAN_ENGINE=mock GPIOZERO_PIN_FACTORY=mock bash systems/scripts/run-backend.sh
```

บน Pi ให้ใช้ `CAMERA_SOURCE=csi-rgb HANDCLEAN_ENGINE=real` แล้วตั้ง `camera.csi_rgb` ตามหัวข้อภาพสีข้างต้น ทดสอบด้วย GPIO mock ก่อน และใช้ `GPIOZERO_PIN_FACTORY=lgpio` เมื่อตรวจอุปกรณ์จริง ส่ง `{"type":"engine_scan_start"}` ผ่าน `ws://localhost:8000/ws` และอ่านผลตาม [WebSocket API](../api.md)

Backend เปิด worker (`backend/engine-check/worker.py` ใน `.venv` ของ engine) **ตัวเดียวตั้งแต่ backend เริ่ม** แบบเบื้องหลัง แล้วใช้ต่อทุกรอบสแกนเหมือน handclean-pi (`EngineWorker` ใน `app/services/engine_process.py`) ส่งภาพ RGB โดยตรงผ่าน pipe

- โหลด engine (MediaPipe + ตารางสีคราบ) รอได้ไม่เกิน 60 วินาที (laptop ใช้ 4.8 วินาที ยังไม่ได้วัดบน Pi) · backend เปิดรับ kiosk ได้ก่อนโหลดเสร็จ · รอบสแกนรอ engine **ก่อน**เปิดไฟ UV และก่อนเริ่มนับ `engine_scan_seconds` · โหลดไม่สำเร็จ = log error แล้วตอบ `error` (`engine scan failed`) โดยไม่เปิดไฟ ครั้งถัดไปลองโหลดใหม่
- แต่ละรอบเรียก `start_session` ใหม่ (session ที่ถูกยกเลิกค้างไว้จะถูกล้างเอง) · deadline ของทั้งรอบใช้ `engine_scan_seconds` ค่าเริ่มต้น 30 วินาที ส่วนหนึ่งคำสั่งตัดที่ 15 วินาทีแล้ว kill worker
- worker ตายหรือรอบสแกนเกิด error → ทิ้ง worker ตัวนั้น รอบถัดไปเปิดตัวใหม่ · ปิด backend → ดับ UV ก่อน แล้วจึงปิด worker
- worker ส่งบรรทัด `ready` (`mask_backend`, `startup_ms`, `level_colors`) แล้ว backend เขียนลง log · คำตอบแต่ละคำสั่งเป็น JSON หนึ่งบรรทัด ตามด้วย level map `uint8` ขนาด `levels_shape` เมื่อมี
- ภาพสดระหว่างสแกนมีสีคราบหยาบ (`hybrid_labels` + `engine.levels` บนภาพพรีวิว 640 px แบบเดียวกับ handclean-pi ใช้แสดงผลเท่านั้น ไม่ใช่คะแนน), เส้นมือจาก landmark และภาพย่อของภาพที่ถ่ายล่าสุดพร้อมสีคราบ (level map ย่อเหลือ 640 px) วาดใน thread `Stream` ก่อน crop · หลังได้คะแนน ภาพย่อค้างไว้ 15 วินาที · ยกเลิก / หมดเวลา / error ล้างทันที

  สูตรผสมสี: `out = (1 − α) · pixel + α · colour[L]` เฉพาะพิกเซลที่ `1 ≤ L ≤ 5` · บนภาพสดคำนวณเป็น `pixel // 2 + colour[L] // 2` และขยาย level map ลงขนาดจอครั้งเดียวต่อ preview (`LiveLevels`) แต่ละเฟรมจึงแทบไม่เพิ่มเวลา (laptop ภาพ 1920×1440 คราบเต็มสองมือ: 30 ms ต่อเฟรมเท่ากับตอนไม่มี overlay เทียบกับ 139 ms ก่อนแคช · เฟรมแรกของ preview ใหม่ +24 ms)

  | ตัวแปร | คืออะไร | ช่วง | ที่มา |
  |---|---|---|---|
  | `pixel` | สีเดิมของภาพ | RGB 0–255 | กล้อง |
  | `L` | ระดับใน level map (0 นอกมือ, 1–5 คราบ, 6 มือสะอาด, 7 ใช้ไม่ได้) | 0–7 | engine `Level` |
  | `colour[L]` | สีของระดับ | RGB | engine `[display].level_colors` |
  | `α` | ความทึบ | 0.5 ภาพสด, 0.6 ภาพย่อ | ภาพย่อ: handclean-pi `EngineLoop._capture` (ทีม HandClean ตั้งเอง) · ภาพสด: เราตั้งเอง ปัดจาก 0.45 ของ handclean-pi `draw_frame` เป็น 0.5 เพื่อใช้หารครึ่งแบบ `uint8` ที่เร็ว |

- `engine_preview.focus` = ความแปรปรวนของ Laplacian บนภาพพรีวิวขาวดำในกรอบมือ (ไม่เห็นมือใช้ครึ่งกลางภาพ) เหมือน `sharpness()` ของ handclean-pi ใช้ตอนหมุนวงแหวนโฟกัสของ HQ camera: ค่ายิ่งสูงยิ่งคม ไม่มีค่าที่ "ดีพอ" ตายตัว

การจับภาพเต็มความละเอียดและการเปิด engine บน Pi ยังต้องตรวจว่าสำเร็จภายในเวลาที่ตั้งไว้

## Type และเทสต์

- `uv run pyright` — strict ทั้ง `app/` และ `tests/` ต้อง 0 error · stub ของ lib ที่ไม่มี type (`gpiozero`, `simplejpeg`, `linuxpy`) อยู่ใน `backend/typings/` เขียนเฉพาะที่ใช้ จะใช้ API ใหม่ของ lib พวกนี้ต้องเพิ่มใน stub ก่อน
- `uv run pytest` — `tests/conftest.py` ตั้ง mock GPIO + ภาพปลอมให้ทุกเทสต์ · ครอบ: `/ws` (ภาพ, `uv_set`, error, หลุดแล้วดับไฟ), ปิด backend แล้วดับไฟ, config ผิดแล้ว fail, `Latest`, `UvService`, ภาพปลอม, crop / วาดกรอบ
- Pi: `docker-compose.yml` + `docker-compose.pi.yml` (ส่ง `/dev/video0`, `/dev/gpiochip0` เข้า container, `restart: always`)

## Open items

- [ ] **Deployment บน Pi:** spec เดิมกำหนด systemd; ตอนนี้ setup เป็น Docker `restart: always` — ยืนยันว่าใช้ Docker
- [ ] **ผล `v4l2-ctl -d /dev/video0 --list-ctrls`** บน Pi จริง (มี `zoom_absolute` ไหม, ชื่อ control ของ exposure / gain / WB)
- [ ] **ความละเอียดที่ดึงจากกล้อง** (4K / 1080p / 720p) — ตัดสินหลัง benchmark · ทาง HandCleanEngine (`csi-rgb`) ใช้ 1920×1440 ตาม handclean-pi แล้ว (KBON 2026-11-22) ทาง USB ยังรอ
- [ ] **เพดานเวลาเปิดไฟ UV** (เสนอ 60 วินาที แม้เปิดจากหน้า Debug) — ยังไม่ยืนยัน
- [ ] ชื่อ device GPIO บน Pi 5 (`/dev/gpiochip0` หรือ `gpiochip4` ขึ้นกับ kernel)
