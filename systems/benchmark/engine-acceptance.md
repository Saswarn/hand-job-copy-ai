# ตรวจรับ HandCleanEngine บน Raspberry Pi 5

เป้าหมายคือสแกนกล้อง CSI จริงครบฝ่ามือและหลังมือ ได้คะแนนผ่าน WebSocket และตรวจว่าไฟ UV ดับจริงตามเกณฑ์ฮาร์ดแวร์ ใช้ WebSocket client ด้านล่างทดสอบได้โดยไม่ต้องเปิด Electron

## เตรียมการ

1. บันทึก Git commit, Raspberry Pi OS, รุ่นกล้องและเลนส์, โหมดภาพและ FPS, ค่า exposure/gain/white balance, รุ่นไฟ UV และรีเลย์ที่ใช้
2. ติดตั้ง `build-essential` แล้วรัน `bash systems/scripts/setup-engine.sh` บน Pi เครื่องนั้นเอง ตรวจว่า smoke test และ pytest ของ engine ผ่านบน arm64 และบรรทัด `engine ready:` มี `'mask_backend': 'grabcut_cpp'`
3. ติดตั้ง backend ตามสคริปต์เดิม ตั้งกล้อง CSI และตรวจว่า backend import Picamera2 ได้ ตั้ง `camera.csi_rgb` (ขนาดภาพ, sensor mode, ค่าที่ล็อก) และ `camera.capture.fps` ให้ตรงชุดที่จะตรวจรับตาม [สเปก Backend](../spec/backend.md) · log ตอนเปิด backend ต้องมี `HandCleanEngine real ready in … ms, mask backend grabcut_cpp` และขนาดภาพของกล้อง (HQ: 1920x1440)
4. ปรับโฟกัส: เปิดหน้า Debug AI กดเริ่มสแกน วางมือในตำแหน่งจริง แล้วหมุนวงแหวนโฟกัสจนค่า "ความคมชัด (focus)" สูงสุด
5. ก่อนใช้ไฟจริง ให้ยืนยันชนิดแหล่งกำเนิดแสง ขั้วควบคุมรีเลย์ และสถานะค่าเริ่มต้นเมื่อ Pi ปิดหรือ GPIO ถูกปล่อย หากข้อมูลฮาร์ดแวร์ยังไม่ครบ ให้ใช้ GPIO mock หรือตัวแทนหลอดที่เห็นสถานะได้ตามขั้นตอนของทีมฮาร์ดแวร์ ดู [Hardware Open items](../hardware.md#open-items)

## ตรวจกล้องและผลคะแนน

เปิด backend บน Pi จากโฟลเดอร์โปรเจกต์ โดยใช้ GPIO mock ในการทดลองกล้องครั้งแรก

```bash
CAMERA_SOURCE=csi-rgb HANDCLEAN_ENGINE=real GPIOZERO_PIN_FACTORY=mock bash systems/scripts/run-backend.sh
```

เปิดอีก Terminal แล้วทดสอบคำสั่งต่อไปนี้จากโฟลเดอร์ `backend` เริ่มด้วย `disconnect` เพื่อตรวจข้อความสถานะ จากนั้นใช้ `timeout` โดยไม่วางมือ และใช้ `complete` โดยทำตาม guidance ให้ครบสองด้าน

```bash
uv run python -m app.engine_scan_check --expect disconnect
uv run python -m app.engine_scan_check --expect timeout
uv run python -m app.engine_scan_check --expect complete
```

- รัน `--expect complete` ซ้ำสองครั้งติดกัน ครั้งที่สองต้องไม่ต้องรอโหลด engine (engine เปิดค้างไว้)
- บนจอ Debug AI ตรวจว่าเห็นสีคราบหยาบและเส้นมือตามมือ และหลังถ่ายแต่ละด้านมีภาพย่อพร้อมสีคราบที่มุมขวาล่าง
- เครื่องมือตรวจว่ามี JPEG ระหว่างสแกน ได้สถานะและคะแนนตามที่คาด และ backend รายงาน UV ดับ เครื่องมือรายงานมัธยฐานของ `timings_ms.total` สำหรับพรีวิวและภาพนิ่งแยกกัน เวลานี้วัดใน engine ส่วน `elapsed` รวมการรอผู้ใช้และการรับส่งข้อความ
- เมื่อทีมฮาร์ดแวร์ยืนยันชุดไฟและรีเลย์แล้ว ทำซ้ำกับ GPIO จริงด้วย `GPIOZERO_PIN_FACTORY=lgpio` ตามขั้นตอนของทีม ยืนยันจาก metadata ว่า exposure, gain และ white balance ที่กล้องใช้จริงคงที่ ตรวจความละเอียดและ FPS ที่จับได้
- ใช้ชุดควบคุมที่รู้สภาพมือ เช่น มือสะอาดและตัวอย่างคราบที่ตกลงกัน สแกนซ้ำภายใต้การตั้งค่าเดียวกันและบันทึกคะแนนทั้งสี่มุม คำเตือน และความสม่ำเสมอของผล ถ้าเกณฑ์สีเดิมไม่ตรงกับชุดกล้องและแสง ให้แยกรอบปรับเทียบก่อนตัดสินผล ห้ามนำเกณฑ์ binary ของ YOLO มาตีความคะแนน engine โดยอัตโนมัติ

## ตรวจการดับไฟจริง

วัดด้วยวิดีโอ slow motion หรือเครื่องมือของทีมฮาร์ดแวร์ ให้เห็นเหตุการณ์เริ่มจับเวลาและสถานะของไฟทุกช่องในหลักฐานเดียวกัน สำหรับกรณี process ถูก kill ให้เริ่มด้วยโหลดทดสอบที่ปลอดภัยและคงสิทธิ์ในการตัดไฟจากฮาร์ดแวร์ ดูวิธีวัดใน [Metrics Hardware](../metrics/hardware.md)

| กรณี | เกณฑ์ผ่าน |
|---|---|
| สแกนเสร็จ `HW-2` | ดับทุกช่องภายใน 500 ms |
| Backend process ถูก kill `HW-3` | ดับทุกช่องภายใน 1 s โดยวัดจากการตัด process จริง |
| WebSocket หลุดระหว่างสแกน `HW-4` | ดับทุกช่องภายใน 2 s |
| สแกนหมดเวลา หรือ engine เกิดข้อผิดพลาด | ดับทุกช่องและ backend รายงานสถานะที่ตรงกับเหตุการณ์ |

เมื่อตรวจครบ ให้บันทึกผลวัดจริงใน [Metrics Hardware](../metrics/hardware.md) และบันทึกความเร็วกับผลการตรวจคราบใน [Metrics AI](../metrics/ai.md) พร้อมระบุวิธีวัดและโปรไฟล์กล้อง เกณฑ์ของ engine ต้องระบุแยกจากเกณฑ์ YOLO เดิม
