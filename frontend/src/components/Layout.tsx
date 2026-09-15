import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { MobileNav } from './MobileNav'

export function Layout() {
  return (
    <div className="flex h-screen bg-[#F7F6F3] overflow-hidden">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <MobileNav />
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
