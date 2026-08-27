import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { OptionsAnalytics } from './pages/OptionsAnalytics'
import { SafetyLogs } from './pages/SafetyLogs'
import { SniperHud } from './pages/SniperHud'
import { TheLab } from './pages/TheLab'
import { TheLedger } from './pages/TheLedger'

/**
 * Command Center app shell (2026-08-27 restructure).
 *
 * Replaces the standalone Sniper HUD app (and the legacy vanilla-JS
 * `dashboard` service it used to run alongside on port 3000) with one
 * unified React app on five routes, all behind a persistent Layout
 * (Sidebar + sticky Zone 1 header -- see Layout.tsx). Only Sniper HUD
 * (`/`) is fully real right now; the other four are clean skeletons
 * per this restructure's own Phase 4 rule -- see each page's own file
 * for what's still a placeholder.
 */
function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<SniperHud />} />
          <Route path="analytics" element={<OptionsAnalytics />} />
          <Route path="optimizer" element={<TheLab />} />
          <Route path="journal" element={<TheLedger />} />
          <Route path="safety" element={<SafetyLogs />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
