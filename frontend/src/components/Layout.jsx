import { NavLink } from 'react-router-dom'
import { LayoutDashboard, Users, Calendar, Video, Settings } from 'lucide-react'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: '仪表盘' },
  { to: '/bloggers', icon: Users, label: '博主管理' },
  { to: '/calendar', icon: Calendar, label: '内容日历' },
  { to: '/videos', icon: Video, label: '视频库' },
  { to: '/settings', icon: Settings, label: '系统设置' },
]

export default function Layout({ children }) {
  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <nav className="w-56 bg-gray-900 border-r border-gray-800 flex flex-col">
        <div className="p-5 border-b border-gray-800">
          <h1 className="text-lg font-bold text-blue-400">AI内容工厂</h1>
          <p className="text-xs text-gray-500 mt-1">短视频智能生产系统</p>
        </div>
        <div className="flex-1 py-4">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-5 py-3 text-sm transition-colors ${
                  isActive
                    ? 'bg-blue-600/20 text-blue-400 border-r-2 border-blue-500'
                    : 'text-gray-400 hover:text-white hover:bg-gray-800'
                }`
              }
            >
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </div>
        <div className="p-4 border-t border-gray-800 text-xs text-gray-600">
          v0.1.0
        </div>
      </nav>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto bg-gray-950 p-6">
        {children}
      </main>
    </div>
  )
}
