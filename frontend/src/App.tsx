import { QueryClientProvider } from '@tanstack/react-query'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { lazy, Suspense, type ReactNode } from 'react'
import { Toaster } from 'react-hot-toast'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { Spinner } from '@/components/ui/primitives'
import { createQueryClient } from '@/lib/queryClient'
import { LoginPage } from '@/pages/LoginPage'
import { useAuthStore } from '@/stores/authStore'

const DashboardPage = lazy(() => import('@/pages/DashboardPage').then((module) => ({ default: module.DashboardPage })))
const CamerasPage = lazy(() => import('@/pages/CamerasPage').then((module) => ({ default: module.CamerasPage })))
const CameraViewPage = lazy(() => import('@/pages/CameraViewPage').then((module) => ({ default: module.CameraViewPage })))
const MapViewPage = lazy(() => import('@/pages/MapViewPage').then((module) => ({ default: module.MapViewPage })))
const AlertsPage = lazy(() => import('@/pages/AlertsPage').then((module) => ({ default: module.AlertsPage })))
const EventsPage = lazy(() => import('@/pages/EventsPage').then((module) => ({ default: module.EventsPage })))
const VideoAnalysisPage = lazy(() => import('@/pages/VideoAnalysisPage').then((module) => ({ default: module.VideoAnalysisPage })))
const EvidencePage = lazy(() => import('@/pages/EvidencePage').then((module) => ({ default: module.EvidencePage })))
const ZoneManagerPage = lazy(() => import('@/pages/ZoneManagerPage').then((module) => ({ default: module.ZoneManagerPage })))
const HardwarePage = lazy(() => import('@/pages/HardwarePage').then((module) => ({ default: module.HardwarePage })))
const AnalyticsPage = lazy(() => import('@/pages/AnalyticsPage').then((module) => ({ default: module.AnalyticsPage })))
const SettingsPage = lazy(() => import('@/pages/SettingsPage').then((module) => ({ default: module.SettingsPage })))

const queryClient = createQueryClient()

function RequireAuth({ children }: { children: ReactNode }) {
  const signedIn = useAuthStore((state) => Boolean(state.accessToken && state.user))
  const location = useLocation()
  if (!signedIn) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  return <>{children}</>
}

function Page({ children }: { children: ReactNode }) {
  return <Suspense fallback={<Spinner className="h-full py-20" label="Loading module…" />}>{children}</Suspense>
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipPrimitive.Provider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              element={
                <RequireAuth>
                  <AppShell />
                </RequireAuth>
              }
            >
              <Route index element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<Page><DashboardPage /></Page>} />
              <Route path="/cameras" element={<Page><CamerasPage /></Page>} />
              <Route path="/cameras/:cameraId" element={<Page><CameraViewPage /></Page>} />
              <Route path="/map" element={<Page><MapViewPage /></Page>} />
              <Route path="/alerts" element={<Page><AlertsPage /></Page>} />
              <Route path="/events" element={<Page><EventsPage /></Page>} />
              <Route path="/evidence" element={<Page><EvidencePage /></Page>} />
              <Route path="/analysis" element={<Page><VideoAnalysisPage /></Page>} />
              <Route path="/zones" element={<Page><ZoneManagerPage /></Page>} />
              <Route path="/hardware" element={<Page><HardwarePage /></Page>} />
              <Route path="/analytics" element={<Page><AnalyticsPage /></Page>} />
              <Route path="/settings" element={<Page><SettingsPage /></Page>} />
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
        <Toaster
          position="bottom-right"
          toastOptions={{
            style: { fontSize: '12px', borderRadius: '10px', whiteSpace: 'pre-line', maxWidth: 380 },
          }}
        />
      </TooltipPrimitive.Provider>
    </QueryClientProvider>
  )
}
