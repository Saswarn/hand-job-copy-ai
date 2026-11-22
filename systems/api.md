# API: frontend ↔ backend

Base: `http://localhost:8000` · WebSocket: `ws://localhost:8000/ws`

สถานะ: ✅ = มีในโค้ดแล้ว · 📝 = ตกลงรูปแบบแล้ว ยังไม่ทำในโค้ด · TODO = ยังไม่ออกแบบ (อัปเดตไฟล์นี้ใน PR เดียวกับโค้ด)

## REST

| Method | Path | สถานะ | รายละเอียด |
|---|---|---|---|
| GET | `/health` | ✅ | `{"status": "ok"}` |
| GET | `/config` | ✅ | คืนค่าตั้งค่าปัจจุบันทั้งหมดในรูปแบบ `AppConfig` (Python: `backend/app/core/config.py` · TypeScript: `frontend/src/renderer/src/common/api.ts`) |
| PUT | `/config` | ✅ | รับค่าตั้งค่าทั้งชุดในรูปแบบ `AppConfig` · ค่าไม่ถูกต้องหรือมีชื่อช่องที่ระบบไม่รู้จัก → `422` · บันทึกลง `config.json` และอัปเดตค่าที่ `GET /config` คืนสำเร็จ → `204` ไม่มีข้อมูลแนบกลับมา |

## WebSocket `/ws`

- **ข้อความ binary** (backend → frontend) = ภาพสด 1 เฟรม เป็น JPEG ล้วน
- **ข้อความ text** = JSON มี field `type` บอกชนิด · schema อยู่ใน `backend/app/schemas/ws.py` · TS + ตัวตรวจ (`parseServerMessage`) อยู่ใน `frontend/src/renderer/src/common/api.ts` · แก้ฝั่งหนึ่งต้องแก้อีกฝั่งใน PR เดียวกัน
- ช่องไฟ UV (`channel`, `channels[i]`) เป็น index เริ่มที่ 0 ตามลำดับใน `uv_gpio` (0 = UV 1)

### ภาพสด (binary)

| สถานะ | รายละเอียด |
|---|---|
| ✅ | backend crop ตาม `camera.crop` และวาดกรอบ AI ทับลงบนภาพแล้ว frontend แค่แสดงผล ไม่ต้องแปลงพิกัด ไม่ต้องหน่วงภาพ |
| ✅ | ส่งเฉพาะเฟรมล่าสุด ถ้า client รับไม่ทันจะข้ามเฟรม ไม่ต่อคิว |
| ✅ | กรอบสี: `hand` เขียว, `dirt` แดง · ตอนนี้ยังไม่มีกรอบ: `Detector` ใน `backend/app/services/detector.py` คืนค่าว่างจนกว่าจะต่อโมเดล |

ขนาดภาพ: กว้างอย่างน้อย `stream.min_width` แต่ไม่ถึง 2 เท่า (หรือเท่าภาพที่ crop ได้ ถ้ากล้องเล็กกว่า) · frontend ควรย่อ / ขยายให้พอดีจอเอง

### backend → frontend (text)

| `type` | สถานะ | รูปแบบ | ส่งเมื่อ |
|---|---|---|---|
| `uv` | ✅ | `{"type": "uv", "channels": [false, false, false]}` | ทันทีที่ต่อ, หลังสั่ง `uv_set`, เมื่อเริ่มสแกน และหลังดับไฟตอนจบสแกน |
| `error` | ✅ | `{"type": "error", "message": "..."}` | ข้อความผิดรูปแบบ, คำสั่งที่ยังไม่ทำ, เริ่มสแกนซ้ำ หรือสแกนเกิดข้อผิดพลาด |
| `scan` | ✅ | `{"type": "scan", "state": "idle" \| "scanning" \| "done", "remaining_s": 3.2 \| null, "result": "clean" \| "dirt" \| null}` | เริ่มสแกน, ประมาณทุก 0.5 วินาทีระหว่างสแกน และตอนจบ |

### frontend → backend (text)

| `type` | สถานะ | รูปแบบ | ผล |
|---|---|---|---|
| `uv_set` | ✅ | `{"type": "uv_set", "channel": 0, "on": true}` | เปิด / ปิดไฟ UV ช่องเดียว (หน้า Debug) แล้วตอบ `uv` |
| `scan_start` | ✅ | `{"type": "scan_start"}` | backend เปิด UV ทุกช่อง + นับ `scan_seconds` + ตัดสินผลด้วย `services/scan.judge` แล้วดับเอง · เริ่มซ้ำขณะสแกนตอบ `error` |
| `binary_view` | 📝 | `{"type": "binary_view", "on": true}` | สลับภาพสดเป็นภาพ binary ที่ป้อนเข้า AI (หน้า Debug) · ตอนนี้ตอบ `error` |

### ลำดับข้อความสแกน (HAN-17)

1. รับ `scan_start` → เปิด UV ทุกช่อง → ส่ง `uv` ที่ทุกช่องเป็น `true`
2. อ่าน `Detector.latest()` เก็บผลเป็นเฟรม และส่ง `scan` ที่ `state="scanning"`, `remaining_s` เป็นเวลาที่เหลือ, `result=null` ประมาณทุก 0.5 วินาที
3. ครบ `scan_seconds` → เรียก `judge()` → ดับ UV → ส่ง `scan` ที่ `state="done"`, `remaining_s=null`, `result="clean"` หรือ `"dirt"` → ส่ง `uv` ที่ทุกช่องเป็น `false`

ภาพ JPEG binary อาจแทรกระหว่างข้อความ text ได้ · ยังไม่มีการส่ง `idle` อัตโนมัติ

- ขณะสแกนยังรับข้อความอื่นได้ เช่น `uv_set` · `scan_start` ซ้ำตอบ `error` (`scan already in progress`) โดยไม่เริ่มใหม่
- สแกนเกิดข้อผิดพลาด → ดับไฟ, log ข้อผิดพลาด, ส่ง `error` (`scan failed`) และ `uv` ถ้ายังส่งได้ โดยไม่ส่ง `done`
- socket หลุด → ดับไฟทันที ยกเลิกและรอ task สแกนกับ task ส่งภาพ · cleanup ของ scan ดับไฟซ้ำใน `finally`
- ใช้ config ชุดปัจจุบันตอนเริ่มแต่ละรอบ; การเปลี่ยน config ไม่เปลี่ยนเกณฑ์ของรอบที่กำลังทำ
- `Detector` ยังเป็น stub และไม่มีเลขเฟรม/เวลาผล AI: รอบนี้เก็บ snapshot ล่าสุดตามจังหวะ polling ไม่สามารถยืนยันว่าทุก snapshot เป็นภาพใหม่ การเชื่อม AI จริงต้องจัดการเรื่องนี้แยกต่างหาก

### สแกนด้วย HandCleanEngine

ส่ง `{"type":"engine_scan_start"}` ผ่าน `/ws` เพื่อเริ่มรอบแบบ engine เส้นทางนี้เปิดด้วย `HANDCLEAN_ENGINE=real` หรือ `HANDCLEAN_ENGINE=mock` และต้องใช้แหล่งภาพ RGB หากยังไม่ได้เปิดโหมดนี้จะตอบ `error` ว่า `engine scan unavailable` คำสั่ง `scan_start` เดิมยังใช้ลำดับ HAN-17

| `type` | ข้อมูล | ส่งเมื่อ |
|---|---|---|
| `engine_preview` | `frame_id`, `hands_found`, `side`, `stable_ms`, `ready_to_capture`, `needed_sides`, `guidance`, `timings_ms` | engine ประเมินเฟรมพรีวิวใหม่ |
| `engine_capture` | `status` (`ok`, `NO_HAND`, `ONE_HAND`), `side`, `hands` (มือ, คะแนน และสัดส่วนคราบ 5 ระดับ), `warnings`, `timings_ms` | มีการวิเคราะห์ภาพเต็มความละเอียด |
| `engine_result` | `status` (`ok`, `incomplete`), `S`, `S_worst`, `worst_view`, `missing_views`, `reason` (`null` หรือ `timeout`) | ได้ผลจบจาก engine หรือหมดเวลาสแกน คะแนนเป็น `null` เมื่อหมดเวลา |

Backend ส่งเฉพาะ metadata ของผลสแกนและยังส่งภาพสดเป็น JPEG ตามเดิม ผล `level_map` ยังไม่อยู่ใน WebSocket contract รอบนี้ ผู้ทดสอบสามารถอ่านผลผ่าน WebSocket client ได้

- ใช้เฟรมใหม่หลังเปิด UV เท่านั้น สแกนสำเร็จหนึ่งครั้งต่อด้านฝ่ามือและหลังมือ การจับภาพล้มเหลวลองใหม่ได้ภายใน `engine_scan_seconds` (ค่าเริ่มต้น 30 วินาที)
- WebSocket ทุกตัวใช้สิทธิ์สแกนร่วมกันหนึ่งรอบ ระหว่าง engine scan จะปฏิเสธ `uv_set` และคำสั่งเริ่มสแกนซ้ำ
- WebSocket ที่เริ่ม engine scan ส่ง `{"type":"engine_scan_cancel"}` เพื่อหยุดและดับ UV ได้ หน้า Debug AI ส่งคำสั่งนี้เมื่อกดหยุดหรือออกจากหน้าระหว่างสแกน
- เมื่อจบ หมดเวลา หรือเกิดข้อผิดพลาด backend ดับ UV ก่อนส่งผลหรือ `error` หาก WebSocket หลุด backend ดับ UV และยกเลิกรอบทันที หาก engine ยังทำงานอยู่จะรอให้ worker ปิด session ก่อนรับรอบใหม่

## กติกาที่ตกลงแล้ว

- ✅ WebSocket ตัวไหนก็ตามหลุด → backend ดับไฟ UV ทุกช่องทันที
- ✅ backend เริ่มทำงาน / ปิด → ไฟ UV ทุกช่องดับ
- ✅ กรอบ AI วาดบนภาพที่ backend แล้ว (ไม่ส่งพิกัดให้ frontend)
