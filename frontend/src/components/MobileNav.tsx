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
      <header className="flex items-center justify-between px-4 h-14 border-b border-[#1f1f1f] bg-[#0a0a0a]">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-md bg-white/5 border border-white/10">
            <Zap className="w-3.5 h-3.5 text-white" />
          </div>
          <span className="text-sm font-semibold text-[#ededed]">LLM Gateway</span>
        </div>
        <button
          onClick={() => setOpen(!open)}
          className="p-2 rounded-md hover:bg-white/5 text-[#888] transition-colors"
        >
          {open ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>
      </header>

      {open && (
        <div className="absolute top-14 left-0 right-0 z-50 border-b border-[#1f1f1f] bg-[#0a0a0a] p-3 flex flex-col gap-0.5">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors',
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
        </div>
      )}
    </div>
  )
}
