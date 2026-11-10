import { useEffect, useRef, useState } from 'react'
import { ExternalLink, Loader2, RefreshCw } from 'lucide-react'

export const LOGIN_PLATFORMS = new Set(['douyin', 'xiaohongshu', 'kuaishou', 'bilibili'])

export default function PlatformConnection({ platform, statusBadge, loadState, onStateChange }) {
  const supported = LOGIN_PLATFORMS.has(platform.id) && platform.automation?.enabled !== false
  const [connection, setConnection] = useState(null)
  const [loading, setLoading] = useState(supported)
  const [unavailable, setUnavailable] = useState('')
  const pending = useRef(false)
  const mounted = useRef(true)

  async function refresh() {
    if (!supported || !loadState || pending.current) return
    pending.current = true
    setLoading(true)
    try {
      const result = await loadState(platform.id)
      if (mounted.current) { setConnection(result); setUnavailable(''); onStateChange?.(platform.id, result) }
    } catch (error) {
      if (mounted.current) { setUnavailable(error.status === 404 ? '登录接口尚未就绪' : '登录状态暂不可用，请稍后刷新'); onStateChange?.(platform.id, null) }
    } finally {
      pending.current = false
      if (mounted.current) setLoading(false)
    }
  }

  useEffect(() => {
    mounted.current = true
    refresh()
    const timer = supported ? setInterval(() => { if (document.visibilityState === 'visible') refresh() }, 10000) : null
    return () => { mounted.current = false; if (timer) clearInterval(timer) }
  }, [platform.id, supported, loadState, onStateChange])

  const authenticated = connection?.authenticated === true && connection?.session_active !== false
  const loginLabel = unavailable
    ? unavailable
    : authenticated
      ? `已登录${connection.display_name ? ` · ${connection.display_name}` : ''}`
      : connection?.status || (loading ? '读取登录状态…' : '等待扫码登录')
  const platformName = platform.name || platform.id
  const loginUrl = `/api/factory/platforms/${encodeURIComponent(platform.id)}/login`

  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-slate-300">{platformName}</span>
        {statusBadge}
      </div>
      {platform.description && <p className="mt-1.5 text-[10px] leading-relaxed text-slate-500">{platform.description}</p>}
      {supported && <div className="mt-3 rounded-lg border border-white/[0.06] bg-white/[0.015] p-2.5">
        <div className="mb-2 flex items-start justify-between gap-2">
          <p className={`min-w-0 break-words text-[10px] leading-relaxed ${!unavailable && authenticated ? 'text-emerald-200/80' : 'text-slate-400'}`}>{loginLabel}</p>
          <button type="button" onClick={refresh} disabled={loading} aria-label={`刷新${platformName}登录状态`} title="刷新登录状态" className="shrink-0 rounded p-0.5 text-slate-500 hover:text-slate-200 disabled:opacity-40">
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
          </button>
        </div>
        <a href={loginUrl} target="_blank" rel="noopener noreferrer" aria-label={`打开${platformName}扫码登录页`} className="inline-flex items-center gap-1.5 rounded-lg border border-sky-300/15 bg-sky-300/5 px-2.5 py-1.5 text-[11px] text-sky-300 hover:bg-sky-300/10">打开扫码登录页<ExternalLink size={11} /></a>
        <p className="mt-2 text-[10px] leading-relaxed text-slate-500">在新页面用官方 App 扫码确认，二维码可在页面刷新。</p>
        <p className="mt-1 text-[10px] leading-relaxed text-slate-500">{platform.status === 'connected' ? '已验证提交，最终审核以平台为准。' : '账号登录后，发布通道仍需验证。'}</p>
      </div>}
    </div>
  )
}
