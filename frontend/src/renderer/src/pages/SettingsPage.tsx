import { Link, useNavigate } from 'react-router'
import { ROUTES } from '../common/routes'
import { useBackend } from '../hooks/useBackend'

/**
 * Settings / Debug (staff only). Sections are placeholders for the Settings tasks:
 * - กล้อง: LiveView + binary_view toggle + camera fields of GET/PUT /config
 * - ไฟ UV: one toggle per channel via { type: 'uv_set' }, all off when leaving this page
 * - การสแกน: scan_seconds, dirt_conf, dirt_min_frames via GET/PUT /config
 */
export default function SettingsPage(): React.JSX.Element {
  const navigate = useNavigate()
  const { uv } = useBackend()

  return (
    <main className="flex h-full w-full flex-col gap-6 p-10">
      <header className="flex items-center justify-between">
        <h1 className="text-title font-bold">Settings / Debug</h1>
        <button
          type="button"
          className="rounded-2xl border-2 border-ink px-10 py-4 text-body"
          onClick={() => navigate(ROUTES.tutorial)}
        >
          กลับ
        </button>
      </header>
      <div className="grid flex-1 grid-cols-3 gap-6">
        <section aria-labelledby="s-camera" className="rounded-2xl border-2 border-muted p-6">
          <h2 id="s-camera" className="text-body font-bold">
            กล้อง
          </h2>
        </section>
        <section aria-labelledby="s-uv" className="rounded-2xl border-2 border-muted p-6">
          <h2 id="s-uv" className="text-body font-bold">
            ไฟ UV
          </h2>
          <p className="text-body text-muted">
            {uv ? uv.map((on, i) => `${i + 1}: ${on ? 'เปิด' : 'ปิด'}`).join(' · ') : '—'}
          </p>
        </section>
        <section aria-labelledby="s-scan" className="rounded-2xl border-2 border-muted p-6">
          <h2 id="s-scan" className="text-body font-bold">
            การสแกน
          </h2>
          <Link
            className="mt-4 inline-block rounded-xl border-2 border-ink px-5 py-2"
            to={ROUTES.debugAi}
          >
            Debug AI
          </Link>
        </section>
      </div>
    </main>
  )
}
