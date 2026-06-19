import { NavLink } from 'react-router-dom'
import { MessageSquare, BarChart2, Zap } from 'lucide-react'
import { cn } from '@/lib/utils'

const navItems = [
  { to: '/', label: 'Query', icon: MessageSquare },
  { to: '/dashboard', label: 'Dashboard', icon: BarChart2 },
]

export function Sidebar() {
  return (
    <aside className="hidden md:flex flex-col w-56 shrink-0 border-r border-[#1f1f1f] bg-[#0a0a0a] h-screen sticky top-0">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 h-14 border-b border-[#1f1f1f]">
        <div className="flex items-center justify-center w-7 h-7 rounded-md bg-white/5 border border-white/10">
          <Zap className="w-3.5 h-3.5 text-white" />
        </div>
        <span className="text-sm font-semibold text-[#ededed] tracking-tight">LLM Gateway</span>
      </div>

      {/* Nav */}
      <nav className="flex flex-col gap-0.5 p-3 flex-1">
        {navItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                isActive
                  ? 'bg-white/8 text-[#ededed] font-medium'
                  : 'text-[#888] hover:text-[#ededed] hover:bg-white/5'
              )
            }
          >
            <Icon className="w-4 h-4 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-[#1f1f1f]">
        <p className="text-[11px] text-[#444]">v1.0.0</p>
      </div>
    </aside>
  )
}
