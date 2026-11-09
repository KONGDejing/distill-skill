import { useEffect, useRef, useState } from 'react'
import { AlertCircle, Loader2, RefreshCw, Send, Upload } from 'lucide-react'
import { factoryApi } from '../api/factory'

const TERMINAL = new Set(['prepared', 'submitted', 'failed'])
const STATUS_LABELS = {
  loading: '读取发布状态', queued: '发布任务排队中', running: '发布任务处理中',
  preparing: '上传准备中', uploading: '视频上传中', submitting: '正在提交平台',
  prepared: '已上传待提交', submitted: '已提交待平台审核', failed: '发布任务未完成',
}
const STATUS_ORDER = { loading: 0, queued: 1, running: 2, preparing: 2, uploading: 2, submitting: 2, prepared: 3, submitted: 4, failed: 4 }

function readJobId(itemId, platform) {
  try { return localStorage.getItem(`factory:${platform}:${itemId}`) } catch { return null }
}
function rememberJob(itemId, platform, jobId) {
  try { localStorage.setItem(`factory:${platform}:${itemId}`, jobId) } catch { /* Polling still works when browser storage is unavailable. */ }
}

export default function PlatformPublisher({ itemId, platform, platformName, initialJob, publicationJobId, packageStatus, connectionState }) {
  const [job, setJob] = useState(() => {
    if (initialJob?.id) return initialJob
    const id = publicationJobId || readJobId(itemId, platform)
    return id ? { id, status: 'loading' } : null
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const inFlight = useRef(false)
  const seenJobIds = useRef(new Set([initialJob?.id, publicationJobId, readJobId(itemId, platform)].filter(Boolean)))
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  useEffect(() => {
    const incoming = initialJob?.id ? initialJob : publicationJobId ? { id: publicationJobId, status: packageStatus || 'loading' } : null
    if (!incoming) return
    setJob(current => {
      // A slower batch refresh must not replace a just-created submission with its older upload job.
      if (current?.id !== incoming.id && seenJobIds.current.has(incoming.id)) return current
      const sameJob = current?.id === incoming.id
      if (sameJob && !TERMINAL.has(current.status) && TERMINAL.has(incoming.status) && !incoming.result) return current
      const resumed = sameJob && current.status === 'failed' && ['queued', 'running'].includes(incoming.status)
      if (sameJob && !resumed && (STATUS_ORDER[current.status] || 0) > (STATUS_ORDER[incoming.status] || 0)) return current
      seenJobIds.current.add(incoming.id)
      rememberJob(itemId, platform, incoming.id)
      return sameJob && current.status === incoming.status ? { ...current, ...incoming } : incoming
    })
  }, [itemId, platform, initialJob?.id, initialJob?.status, initialJob?.message, initialJob?.result?.command_id, publicationJobId, packageStatus])

  useEffect(() => {
    // Batch summaries omit the full result; fetch even a terminal job once before deciding whether retry is safe.
    if (!job?.id || (TERMINAL.has(job.status) && job.result != null)) return
    let active = true
    let polling = false
    const poll = async () => {
      if (polling) return
      polling = true
      try {
        const result = await factoryApi.getPlatformJob(job.id)
        if (active) { setJob(result); setError('') }
      } catch (e) {
        if (active) setError(e.message)
      } finally { polling = false }
    }
    poll()
    const timer = setInterval(poll, 2000)
    return () => { active = false; clearInterval(timer) }
  }, [job?.id, job?.status, job?.result != null])

  async function act(action) {
    if (inFlight.current || (action === 'prepare' && !canPrepare) || (action === 'publish' && !canPublish)) return
    inFlight.current = true
    setBusy(true)
    setError('')
    try {
      const next = action === 'prepare'
        ? await factoryApi.preparePlatform(itemId, platform)
        : action === 'publish'
          ? await factoryApi.publishPlatformJob(job.id)
          : await factoryApi.getPlatformJob(job.id)
      if (!next.id) throw new Error('平台未返回任务编号，请刷新后查看状态。')
      seenJobIds.current.add(next.id)
      rememberJob(itemId, platform, next.id)
      if (mounted.current) setJob(next)
    } catch (e) {
      if (mounted.current) setError(e.message)
    } finally {
      inFlight.current = false
      if (mounted.current) setBusy(false)
    }
  }

  const legacyXiaohongshu = platform === 'xiaohongshu'
  const authenticated = connectionState?.authenticated === true && connectionState?.session_active !== false
  const canPrepare = legacyXiaohongshu || (authenticated && connectionState?.capabilities?.prepare === true)
  const canPublish = legacyXiaohongshu || (authenticated && connectionState?.capabilities?.publish === true)
  const waiting = job && !TERMINAL.has(job.status)
  const submitted = job?.status === 'submitted' || packageStatus === 'submitted'
  const safeToRetry = job?.status === 'failed' && job.result?.submission_attempted === false && !job.result?.submission_uncertain
  const buttonClass = 'inline-flex w-full items-center justify-center gap-1.5 rounded-lg border px-2 py-2 text-[10px] font-medium transition disabled:opacity-40'

  return (
    <div className="mt-3 space-y-2 border-t border-white/[0.06] pt-3">
      {job && <p className={`flex items-start gap-1.5 text-[10px] leading-relaxed ${job.status === 'failed' ? 'text-rose-300' : submitted ? 'text-sky-300' : 'text-amber-200'}`}>
        {waiting && <Loader2 size={11} className="mt-0.5 shrink-0 animate-spin" />}
        {STATUS_LABELS[job.status] || '正在同步发布状态'}
      </p>}
      {!job && !submitted && <button type="button" onClick={() => act('prepare')} disabled={busy || !canPrepare} className={`${buttonClass} border-sky-300/15 bg-sky-300/5 text-sky-300 hover:bg-sky-300/10`}>{busy ? <Loader2 size={11} className="shrink-0 animate-spin" /> : <Upload size={11} className="shrink-0" />}上传并准备发布</button>}
      {job?.status === 'prepared' && !submitted && <button type="button" onClick={() => act('publish')} disabled={busy || !canPublish} className={`${buttonClass} border-emerald-300/20 bg-emerald-300/10 text-emerald-200 hover:bg-emerald-300/20`}>{busy ? <Loader2 size={11} className="shrink-0 animate-spin" /> : <Send size={11} className="shrink-0" />}提交{platformName}</button>}
      {safeToRetry && !submitted && <><button type="button" onClick={() => act('prepare')} disabled={busy || !canPrepare} className={`${buttonClass} border-sky-300/15 bg-sky-300/5 text-sky-300 hover:bg-sky-300/10`}>{busy ? <Loader2 size={11} className="shrink-0 animate-spin" /> : <Upload size={11} className="shrink-0" />}重新上传并准备</button><p className="text-[10px] leading-relaxed text-slate-500">已确认上次未点击提交。重新上传后，可再次提交。</p></>}
      {job?.status === 'failed' && !submitted && <button type="button" onClick={() => act('refresh')} disabled={busy} className={`${buttonClass} border-white/10 text-slate-400 hover:bg-white/5`}>{busy ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}刷新任务状态</button>}
      {!legacyXiaohongshu && !submitted && (!canPrepare || !canPublish) && <div className="space-y-1 text-[10px] leading-relaxed text-slate-500">
        {!authenticated && <p>请先完成官方扫码登录。</p>}
        {connectionState?.capabilities?.prepare !== true && <p>上传准备尚未完成联调。</p>}
        {connectionState?.capabilities?.publish !== true && <p>提交能力尚未完成联调。</p>}
        <a href={`/api/factory/platforms/${encodeURIComponent(platform)}/login`} target="_blank" rel="noopener noreferrer" className="inline-block text-sky-300 hover:text-sky-200">打开{platformName}扫码登录页</a>
      </div>}
      {submitted && <p className="text-[10px] leading-relaxed text-slate-500">提交回执已记录，公开状态以平台审核结果为准。</p>}
      {(error || (job?.status === 'failed' && job.message)) && <p role="alert" className="flex gap-1 break-words text-[10px] leading-relaxed text-rose-300"><AlertCircle size={11} className="mt-0.5 shrink-0" /><span className="min-w-0">{error || job.message}</span></p>}
      {job?.message && job.status !== 'failed' && !submitted && <p className="break-words text-[10px] leading-relaxed text-slate-500">{job.message}</p>}
    </div>
  )
}
