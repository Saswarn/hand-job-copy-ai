import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import type { EngineCapture, EnginePreview, EngineResult, Side } from '../common/api'
import { ROUTES } from '../common/routes'
import LiveView from '../components/LiveView'
import { useBackend } from '../hooks/useBackend'

const sideLabels: Record<Side, string> = { palm: 'ฝ่ามือ', back: 'หลังมือ' }
const instructions = new Map([
  ['SHOW_TWO_HANDS', 'ยื่นมือทั้งสองข้าง'],
  ['SPREAD_FINGERS', 'กางนิ้วออก'],
  ['HOLD_STILL', 'ถือนิ่ง ๆ'],
  ['CAPTURE_NOW', 'กำลังจับภาพ'],
  ['FLIP_HANDS', 'พลิกมือ'],
  ['SIDE_ALREADY_DONE', 'ด้านนี้จับภาพแล้ว ให้พลิกมือ'],
  ['ALL_DONE', 'จับภาพครบแล้ว']
])

export default function DebugAiPage(): React.JSX.Element {
  const { connected, uv, send, subscribeMessages } = useBackend()
  const pending = useRef(false)
  const lastPreview = useRef<{ guidance: string; at: number } | null>(null)
  const [running, setRunning] = useState(false)
  const [preview, setPreview] = useState<EnginePreview | null>(null)
  const [captures, setCaptures] = useState<Partial<Record<Side, EngineCapture>>>({})
  const [result, setResult] = useState<EngineResult | null>(null)
  const [notice, setNotice] = useState('')

  useEffect(() => {
    const unsubscribe = subscribeMessages((message) => {
      if (!pending.current) return
      if (message.type === 'engine_preview') {
        const now = performance.now()
        const last = lastPreview.current
        if (!last || last.guidance !== message.guidance || now - last.at >= 200) {
          lastPreview.current = { guidance: message.guidance, at: now }
          setPreview(message)
        }
      } else if (message.type === 'engine_capture') {
        setCaptures((previous) => ({ ...previous, [message.side]: message }))
      } else if (message.type === 'engine_result') {
        pending.current = false
        setRunning(false)
        setResult(message)
      } else if (
        message.type === 'error' ||
        (message.type === 'uv' && message.channels.every((on) => !on))
      ) {
        pending.current = false
        setRunning(false)
        setNotice(message.type === 'error' ? message.message : 'การสแกนหยุดแล้ว')
      }
    })
    return () => {
      unsubscribe()
      if (pending.current) send({ type: 'engine_scan_cancel' })
      pending.current = false
    }
  }, [send, subscribeMessages])

  function start(): void {
    if (pending.current) return
    setPreview(null)
    setCaptures({})
    setResult(null)
    lastPreview.current = null
    pending.current = send({ type: 'engine_scan_start' })
    setRunning(pending.current)
    setNotice(pending.current ? '' : 'เชื่อมต่อ backend ไม่สำเร็จ')
  }

  return (
    <main className="grid h-full grid-rows-[auto_minmax(0,1fr)] gap-4 p-6">
      <header className="flex flex-wrap items-center justify-between gap-3 pr-20">
        <h1 className="text-title font-bold">Debug AI</h1>
        <div className="flex flex-wrap gap-3">
          <Link className="rounded-xl border-2 border-ink px-5 py-2" to={ROUTES.settings}>
            กลับ Settings
          </Link>
          <button
            className="rounded-xl border-2 border-ink px-5 py-2 font-bold disabled:opacity-50"
            type="button"
            disabled={!connected || !running}
            onClick={() => send({ type: 'engine_scan_cancel' })}
          >
            หยุดสแกน
          </button>
          <button
            className="rounded-xl bg-ink px-5 py-2 font-bold text-surface disabled:opacity-50"
            type="button"
            disabled={!connected || running}
            onClick={start}
          >
            เริ่มสแกน
          </button>
        </div>
      </header>
      <div className="grid min-h-0 gap-4 overflow-auto lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section
          aria-label="ภาพสด"
          className="min-h-56 overflow-hidden rounded-2xl border border-muted"
        >
          <LiveView />
        </section>
        <section
          aria-label="ข้อมูลการสแกน"
          className="space-y-4 overflow-auto rounded-2xl border border-muted p-5"
        >
          <p className="text-sm">
            Backend {connected ? 'เชื่อมต่อแล้ว' : 'หลุดการเชื่อมต่อ'} · UV{' '}
            {uv?.some(Boolean) ? 'เปิด' : uv ? 'ปิด' : 'รอสถานะ'}
          </p>
          <div aria-live="polite">
            <p className="text-body font-bold">
              {running
                ? preview
                  ? (instructions.get(preview.guidance) ?? preview.guidance)
                  : 'กำลังเริ่มสแกน'
                : result
                  ? result.status === 'ok'
                    ? 'สแกนเสร็จ'
                    : 'สแกนไม่ครบ'
                  : 'พร้อมเริ่มสแกน'}
            </p>
            {notice && <p>{notice}</p>}
          </div>
          {preview && (
            <p>
              พบ {preview.hands_found} มือ ·{' '}
              {preview.side && preview.side !== 'unsure'
                ? sideLabels[preview.side]
                : 'ยังไม่ทราบด้าน'}{' '}
              · พรีวิว {preview.timings_ms.total?.toFixed(1) ?? '—'} ms
            </p>
          )}
          {preview?.focus != null && (
            <p>
              ความคมชัด (focus) {preview.focus.toFixed(0)} · หมุนวงแหวนโฟกัสให้ค่านี้สูงสุด
            </p>
          )}
          {(['palm', 'back'] as const).map((side) => {
            const capture = captures[side]
            return (
              <div key={side} className="border-t border-muted pt-3">
                <h2 className="font-bold">{sideLabels[side]}</h2>
                <p>
                  {capture
                    ? `${capture.status} · ${capture.timings_ms.total?.toFixed(1) ?? '—'} ms`
                    : 'รอจับภาพ'}
                </p>
                {capture?.hands.map(({ hand, score }) => (
                  <p key={hand}>
                    {hand === 'left' ? 'ซ้าย' : 'ขวา'} {score.toFixed(2)}
                  </p>
                ))}
                {!!capture?.warnings.length && <p>คำเตือน {capture.warnings.join(', ')}</p>}
              </div>
            )
          })}
          {result && (
            <div className="border-t border-muted pt-3">
              <h2 className="font-bold">ผลการสแกน</h2>
              {result.S !== null && (
                <p className="text-body font-bold">คะแนนรวม {result.S.toFixed(2)}</p>
              )}
              {result.S_worst !== null && (
                <p>
                  คะแนนต่ำสุด {result.S_worst.toFixed(2)} · {result.worst_view}
                </p>
              )}
              {result.reason === 'timeout' && <p>หมดเวลาสแกน</p>}
              {!!result.missing_views.length && <p>ยังขาด {result.missing_views.join(', ')}</p>}
            </div>
          )}
        </section>
      </div>
    </main>
  )
}
