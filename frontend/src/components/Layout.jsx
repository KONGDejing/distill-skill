import { NavLink } from 'react-router-dom'
import { LayoutDashboard, Users, Calendar, Video, Settings, Workflow, Sparkles, ArrowUpRight } from 'lucide-react'

const navItems = [
  { to: '/', icon: Workflow, label: '工厂工作台', caption: 'AGENT WORKSPACE' },
  { to: '/overview', icon: LayoutDashboard, label: '内容总览' },
  { to: '/bloggers', icon: Users, label: '对标博主' },
  { to: '/calendar', icon: Calendar, label: '文案与日历' },
  { to: '/videos', icon: Video, label: '历史视频库' },
  { to: '/settings', icon: Settings, label: '声音与设置' },
]

export default function Layout({ children }) {
  return (
    <div className="flex min-h-screen flex-col text-slate-100 lg:h-screen lg:flex-row lg:overflow-hidden">
      <aside className="shrink-0 border-b border-white/[0.07] bg-[#0c121b] lg:flex lg:w-60 lg:flex-col lg:border-b-0 lg:border-r">
        <div className="flex items-center gap-3 px-5 py-5 lg:px-6 lg:py-8">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-300 text-slate-950 shadow-[0_0_30px_#6ee7b720]">
            <Workflow size={23} strokeWidth={2.3} />
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-wide text-white">短视频 AI 内容工厂</h1>
            <p className="mt-1 text-[9px] font-medium tracking-[0.18em] text-slate-500">SHORT VIDEO AI FACTORY</p>
          </div>
        </div>
        <div className="hidden px-6 pb-3 pt-2 text-[10px] tracking-[0.18em] text-slate-600 lg:block">工作空间</div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-1 lg:flex-col lg:overflow-visible lg:pb-0" aria-label="主要导航">
          {navItems.map(({ to, icon: Icon, label, caption }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) => `group flex shrink-0 items-center gap-3 rounded-xl px-3 py-3 text-sm transition-colors lg:px-4 ${isActive ? 'bg-emerald-300/[0.09] text-emerald-200 ring-1 ring-inset ring-emerald-200/10' : 'text-slate-500 hover:bg-white/[0.035] hover:text-slate-200'}`}
            >
              <Icon size={18} />
              <div><span className="font-medium">{label}</span>{caption && <span className="mt-1 hidden text-[8px] tracking-[0.16em] opacity-50 lg:block">{caption}</span>}</div>
            </NavLink>
          ))}
        </nav>
        <div className="hidden px-5 pb-6 lg:block">
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.025] p-4">
            <Sparkles size={16} className="mb-3 text-emerald-300/70" />
            <p className="text-xs font-medium text-slate-300">把想法，变成持续产出。</p>
            <p className="mt-2 text-[11px] leading-relaxed text-slate-600">从科技情报到视频成片，每一步都有迹可循。</p>
          </div>
          <div className="mt-5 flex items-center justify-between px-1 text-[10px] text-slate-600"><span>FACTORY / v0.2.0</span><ArrowUpRight size={12} /></div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 bg-[#080d14] p-4 sm:p-6 lg:overflow-y-auto lg:p-8 xl:p-10">{children}</main>
    </div>
  )
}
