import { NavLink } from 'react-router-dom'
import { MessageSquare, BarChart2, Zap, FlaskConical } from 'lucide-react'
import { cn } from '@/lib/utils'

const navItems = [
  { to: '/', label: 'Query', icon: MessageSquare },
  { to: '/dashboard', label: 'Dashboard', icon: BarChart2 },
  { to: '/experiments', label: 'Experiments', icon: FlaskConical },
]

export function Sidebar() {
  return (
    <aside className="hidden md:flex flex-col w-56 shrink-0 border-r border-stone-200 bg-white h-screen sticky top-0 shadow-sm">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 h-14 border-b border-stone-200">
        <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-amber-50 border border-amber-200">
          <Zap className="w-3.5 h-3.5 text-amber-700" />
        </div>
        <span className="text-sm font-semibold text-stone-900 tracking-tight">LLM Gateway</span>
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
                'flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all duration-150',
                isActive
                  ? 'bg-amber-50 text-amber-800 font-medium border-l-2 border-amber-600 pl-[10px]'
                  : 'text-stone-500 hover:text-stone-900 hover:bg-stone-100 hover:translate-x-0.5'
              )
            }
          >
            <Icon className="w-4 h-4 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-stone-200">
        <p className="text-[11px] text-stone-400">v1.0.0</p>
      </div>
    </aside>
  )
}
