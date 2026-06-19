import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'sonner'
import { Layout } from '@/components/Layout'
import { QueryPage } from '@/pages/QueryPage'
import { DashboardPage } from '@/pages/DashboardPage'

export default function App() {
  return (
    <BrowserRouter>
      <Toaster
        position="top-right"
        theme="dark"
        toastOptions={{
          style: {
            background: '#111',
            border: '1px solid #262626',
            color: '#ededed',
            fontSize: '13px',
          },
        }}
      />
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<QueryPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
