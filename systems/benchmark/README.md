# Benchmark

สำหรับขั้นตอนทดสอบ engine ผ่าน WebSocket และตรวจการดับ UV บนเครื่องจริง ดู [ตรวจรับ HandCleanEngine บน Raspberry Pi 5](engine-acceptance.md)

วัดเวลาของแต่ละขั้นใน pipeline แยกกัน เพื่อรู้ว่าคอขวดอยู่ตรงไหนก่อนจะเปลี่ยนเครื่องมือ

| ขั้น | วัดอะไร | metric ที่เกี่ยว |
|---|---|---|
| grab | ดึง JPEG ดิบจากกล้อง 1 เฟรม | HW-5, BE-2 |
| decode | แตก JPEG (ย่อขนาดตอน decode) | AI-2 |
| binary | crop + threshold + letterbox 640 | AI-2 |
| infer | onnxruntime 1 เฟรม | AI-1, AI-2 |
| total | ทั้งหมดต่อกัน | AI-2 |

```bash
uv run --project backend python systems/benchmark/bench.py
```

- รัน**บน Pi 5 จริงเท่านั้น** พร้อมกล้องที่ต่ออยู่ และโมเดลใน `backend/models/`
- ลองหลายความละเอียด (`camera.capture` ใน config: 4K / 1080p / 720p) แล้วบันทึกทุกชุด
- สถานะ: โครง — แต่ละขั้นเป็น `TODO` ใน [bench.py](bench.py)
