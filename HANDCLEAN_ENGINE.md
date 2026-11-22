# วิธีติดตั้งและใช้งาน HandCleanEngine

HandCleanEngine วิเคราะห์ภาพฝ่ามือและหลังมือ แล้วส่งคำแนะนำระหว่างสแกน คะแนนรายมือ และคะแนนรวมผ่าน WebSocket ของ backend ใช้ Terminal ตรวจผลได้โดยไม่ต้องเปิด Electron

## 1. ติดตั้ง

เตรียม Git, `uv` และ Python 3.11 ที่สร้าง `venv` ได้ จากโฟลเดอร์หลักของ repo รัน

```bash
bash systems/scripts/setup-backend.sh
bash systems/scripts/setup-engine.sh
```

สคริปต์ดาวน์โหลด engine จาก revision ที่ระบุไว้ ติดตั้งลง `backend/engine-check/.venv/` แล้วรันเทสต์ด้วยภาพสังเคราะห์ หากคำสั่ง `python3.11` ใช้ไม่ได้ ให้ระบุ Python 3.11 ที่ติดตั้งไว้ผ่าน `PYTHON_BIN=/path/to/python3.11` ตอนรันสคริปต์

โค้ด AI ที่ติดตั้งแล้วอยู่ที่ `backend/engine-check/.venv/lib/python3.11/site-packages/handclean_engine/` ส่วน [worker.py](backend/engine-check/worker.py) เป็นไฟล์ใน repo ที่รับภาพจาก backend ส่งให้ engine และส่งผลกลับ

## 2. ทดลองบนเครื่องพัฒนา

เปิด Terminal แรกที่โฟลเดอร์หลัก แล้วรัน backend ด้วยกล้องและ GPIO จำลอง

```bash
CAMERA_SOURCE=fake-rgb HANDCLEAN_ENGINE=mock GPIOZERO_PIN_FACTORY=mock bash systems/scripts/run-backend.sh
```

เปิด Terminal ที่สองจากโฟลเดอร์หลัก แล้วรัน

```bash
cd backend
uv run python -m app.engine_scan_check --expect complete
```

ผลที่คาดคือ `palm: ok`, `back: ok` และ `engine_result` ที่มี `status: "ok"` พร้อมคะแนน `S` ตัวเลขจากโหมดนี้เป็นข้อมูลจำลอง

## 3. ใช้กล้องจริงบน Raspberry Pi 5

1. ติดตั้ง backend และ engine ตามขั้นตอนแรก ใช้ Raspberry Pi OS 64 บิตและกล้อง CSI ที่ทำงานกับ Picamera2 ได้ ตรวจว่า Python ของ backend รันคำสั่ง `uv run python -c "import picamera2"` ได้จากโฟลเดอร์ `backend`
2. แก้ [config.json](backend/config/config.json) ให้ `camera.capture` ตรงกับความละเอียดและ FPS ที่กล้องรองรับ แล้วเพิ่ม `camera.csi_rgb` โดยกำหนด `exposure_us`, `analogue_gain` และ `colour_gains` คู่แดง/น้ำเงินตามค่าที่ปรับเทียบกับกล้องและแสงชุดจริง
3. เปิด backend จากโฟลเดอร์หลัก เริ่มด้วย GPIO จำลองเพื่อตรวจว่ากล้องและ engine ทำงาน

```bash
CAMERA_SOURCE=csi-rgb HANDCLEAN_ENGINE=real GPIOZERO_PIN_FACTORY=mock bash systems/scripts/run-backend.sh
```

จากอีก Terminal รันคำสั่งตรวจผลในข้อ 2 ทำตามคำแนะนำที่แสดง เช่น `HOLD_STILL` ให้ถือนิ่ง และ `FLIP_HANDS` ให้พลิกมือ หากหมดเวลาก่อนครบทั้งสองด้าน ผลจะแสดง `status: "incomplete"`

สำหรับการสแกนกับไฟจริง ให้ยืนยันชุดไฟและรีเลย์ก่อน แล้วทำตาม [คู่มือตรวจรับบน Pi 5](systems/benchmark/engine-acceptance.md) ซึ่งมีขั้นตอนทดสอบ GPIO จริงและการดับไฟ UV

หากใช้ WebSocket client ของคุณเอง ให้เชื่อมต่อ `ws://localhost:8000/ws` แล้วส่ง `{"type":"engine_scan_start"}` ดูฟิลด์ผลลัพธ์ใน [สัญญา WebSocket](systems/api.md)

## ดูผลบนจอ Debug AI

เปิด frontend พร้อม backend ที่เปิดโหมด HandCleanEngine แล้ว กดค้างมุมขวาบน 3 วินาทีเพื่อเข้า **Settings / Debug** จากนั้นเลือก **Debug AI** ในส่วนการสแกน กด **เริ่มสแกน** เพื่อดูภาพสด คำแนะนำ ผลรายด้าน และคะแนนรวม กด **หยุดสแกน** หรือกลับออกจากหน้าเพื่อยกเลิกรอบที่กำลังทำงาน
