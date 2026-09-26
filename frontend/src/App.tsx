import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'sonner'
import { Layout } from '@/components/Layout'
import { QueryPage } from '@/pages/QueryPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { ExperimentsPage } from '@/pages/ExperimentsPage'

export default function App() {
  return (
    <BrowserRouter>
      <Toaster
        position="top-right"
        theme="light"
        toastOptions={{
          style: {
            background: '#FFFFFF',
            border: '1px solid #E7E5E4',
            color: '#1C1917',
            fontSize: '13px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
          },
        }}
      />
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<QueryPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/experiments" element={<ExperimentsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
