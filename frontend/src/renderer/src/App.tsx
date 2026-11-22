import { HashRouter, Navigate, Route, Routes } from 'react-router'
import { ROUTES } from './common/routes'
import BackendProvider from './components/BackendProvider'
import ConnectingOverlay from './components/ConnectingOverlay'
import LongPressCorner from './components/LongPressCorner'
import AiDetectionPage from './pages/AiDetectionPage'
import DebugAiPage from './pages/DebugAiPage'
import SettingsPage from './pages/SettingsPage'
import TutorialPage from './pages/TutorialPage'

function App(): React.JSX.Element {
  return (
    <BackendProvider>
      <HashRouter>
        <Routes>
          <Route path={ROUTES.tutorial} element={<TutorialPage />} />
          <Route path={ROUTES.detection} element={<AiDetectionPage />} />
          <Route path={ROUTES.settings} element={<SettingsPage />} />
          <Route path={ROUTES.debugAi} element={<DebugAiPage />} />
          <Route path="*" element={<Navigate to={ROUTES.tutorial} replace />} />
        </Routes>
        <ConnectingOverlay />
        <LongPressCorner />
      </HashRouter>
    </BackendProvider>
  )
}

export default App
