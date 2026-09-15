import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { MessageSquare, BarChart2, Zap, Menu, X } from 'lucide-react'
import { cn } from '@/lib/utils'

const navItems = [
  { to: '/', label: 'Query', icon: MessageSquare },
  { to: '/dashboard', label: 'Dashboard', icon: BarChart2 },
]

export function MobileNav() {
  const [open, setOpen] = useState(false)

  return (
    <div className="md:hidden">
      <header className="flex items-center justify-between px-4 h-14 border-b border-stone-200 bg-white shadow-sm">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-amber-50 border border-amber-200">
            <Zap className="w-3.5 h-3.5 text-amber-700" />
          </div>
          <span className="text-sm font-semibold text-stone-900">LLM Gateway</span>
        </div>
        <button
          onClick={() => setOpen(!open)}
          className="p-2 rounded-lg hover:bg-stone-100 text-stone-500 transition-all duration-150 active:scale-95"
          aria-label={open ? 'Close menu' : 'Open menu'}
        >
          {open ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>
      </header>

      {open && (
        <div className="absolute top-14 left-0 right-0 z-50 border-b border-stone-200 bg-white p-3 flex flex-col gap-0.5 shadow-lg">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all duration-150',
                  isActive
                    ? 'bg-amber-50 text-amber-800 font-medium border-l-2 border-amber-600 pl-[10px]'
                    : 'text-stone-500 hover:text-stone-900 hover:bg-stone-100'
                )
              }
            >
              <Icon className="w-4 h-4 shrink-0" />
              {label}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  )
}
