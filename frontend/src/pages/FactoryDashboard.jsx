import { useCallback, useEffect, useRef, useState } from 'react'
import { Activity, ArrowDownToLine, ArrowRight, BookOpen, Bot, Check, CheckCircle2, ChevronDown, ChevronRight, Circle, Clock3, ExternalLink, FileText, Film, FlaskConical, History, Layers3, Loader2, Pause, Play, Radio, RefreshCw, Search, Settings2, ShieldCheck, Sparkles, TrendingUp, Workflow, X, AlertCircle, PackageCheck } from 'lucide-react'
import { factoryApi } from '../api/factory'
import XiaohongshuPublisher from '../components/XiaohongshuPublisher'
import PlatformConnection from '../components/PlatformConnection'
import PlatformPublisher from '../components/PlatformPublisher'
import FactoryOperations from '../components/FactoryOperations'

const VISUAL_STYLES = { tech_montage: '科技演示混剪', cards: '文字卡片（旧版）' }
const VOICE_LABELS = { 'cosyvoice2-local-reference': '本地标准音色', 'edge-tts': '系统音色', edge: '系统音色' }
const PLATFORM_NAMES = { douyin: '抖音', xiaohongshu: '小红书', kuaishou: '快手', bilibili: '哔哩哔哩' }
const AGENT_ICONS = { director: Workflow, researcher: Search, writer: FileText, producer: Film, producer2: Film, producer3: Film, reviewer: ShieldCheck, publisher: Radio, analyst: TrendingUp }
const ACTIVE = new Set(['queued', 'running', 'researching', 'planning', 'writing', 'reviewing', 'rendering', 'adapting', 'reporting', 'cancelling', 'cancel_requested', 'processing'])
const RETRYABLE = new Set(['paused_memory', 'failed', 'partial', 'cancelled', 'error', 'interrupted'])
const STATUS_LABELS = {
  scheduled: '已排期', waiting_media: '等待成片', missed: '未按时提交', uploading: '上传中', publishing: '提交处理中', queued: '排队中', planned: '待创作', pending: '待开始', running: '执行中', researching: '采集资料', planning: '选题决策', writing: '创作中', reviewing: '审核中', rendering: '制作中', adapting: '平台适配', reporting: '生成日报', processing: '处理中',
  completed: '已完成', succeeded: '已完成', complete: '已完成', ready: '已就绪', approved: '审核通过', package_ready: '素材包就绪', prepared: '已上传待提交', submitted: '已提交待平台审核', published: '已发布',
  paused_memory: '内存保护暂停', idle: '待命', active: '工作中', busy: '工作中', working: '工作中', online: '待命', waiting: '待任务', paused: '已暂停', offline: '离线', blocked: '待处理',
  rejected: '需返工', failed: '失败', error: '异常', partial: '部分完成', cancelled: '已取消', cancelling: '正在取消', cancel_requested: '正在取消', interrupted: '执行中断',
  not_configured: '待接入', not_connected: '待接入', awaiting_credentials: '待接入', disconnected: '待接入', connected: '已接入', disabled: '未启用', skipped: '已跳过', warning: '提醒', success: '完成', started: '开始', info: '记录',
}
const STAGES = [
  { keys: ['research', 'researching'], label: '情报采集', icon: Search },
  { keys: ['planning', 'plan', 'director'], label: '选题决策', icon: Workflow },
  { keys: ['writing', 'script', 'review', 'reviewing', 'script_review', 'revision'], label: '文案审核', icon: FileText },
  { keys: ['production', 'render', 'rendering', 'video', 'media_review', 'quality'], label: '制作质检', icon: Film },
  { keys: ['adapting', 'publish', 'publishing', 'packaging', 'packages'], label: '平台适配', icon: Layers3 },
  { keys: ['report', 'reporting', 'analysis', 'completed', 'finished'], label: '复盘交付', icon: TrendingUp },
]

function asText(value) {
  if (value == null) return ''
  return typeof value === 'string' ? value : typeof value === 'object' ? JSON.stringify(value) : String(value)
}
function formatDate(value, timeOnly = false) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', ...(timeOnly ? {} : { month: '2-digit', day: '2-digit' }), hour: '2-digit', minute: '2-digit', hour12: false })
}
function safeLink(value) {
  return typeof value === 'string' && (/^https?:\/\//i.test(value) || value.startsWith('/')) ? value : undefined
}
function statusColor(status) {
  if (['completed', 'succeeded', 'ready', 'approved', 'package_ready', 'prepared', 'published', 'success', 'connected'].includes(status)) return 'text-emerald-300 bg-emerald-300/10 border-emerald-300/15'
  if (['failed', 'error', 'blocked'].includes(status)) return 'text-rose-300 bg-rose-300/10 border-rose-300/15'
  if (['partial', 'interrupted', 'warning', 'paused_memory', 'paused'].includes(status)) return 'text-amber-300 bg-amber-300/10 border-amber-300/15'
  if (ACTIVE.has(status) || ['active', 'busy', 'working', 'started', 'submitted'].includes(status)) return 'text-sky-300 bg-sky-300/10 border-sky-300/15'
  return 'text-slate-400 bg-slate-400/5 border-slate-400/10'
}
function Status({ value, small = false }) {
  return <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border font-medium ${small ? 'px-2 py-0.5 text-[10px]' : 'px-2.5 py-1 text-[11px]'} ${statusColor(value)}`}><span className={`h-1 w-1 rounded-full bg-current ${ACTIVE.has(value) ? 'animate-pulse' : ''}`} />{STATUS_LABELS[value] || value || '待开始'}</span>
}
function Empty({ icon: Icon = Layers3, title, description }) {
  return <div className="flex flex-col items-center px-5 py-14 text-center"><div className="mb-4 rounded-2xl border border-white/[0.06] bg-white/[0.025] p-4"><Icon size={25} className="text-slate-500" /></div><p className="text-sm text-slate-400">{title}</p><p className="mt-2 max-w-md text-xs leading-relaxed text-slate-500">{description}</p></div>
}
function Stat({ icon: Icon, label, value, suffix, detail }) {
  return <div className="rounded-2xl border border-white/[0.06] bg-[#0e1621] p-4 sm:p-5"><div className="mb-4 flex items-center justify-between text-xs text-slate-400"><span>{label}</span><Icon size={16} className="text-slate-500" /></div><div className="flex items-baseline gap-2"><span className="text-3xl font-semibold tabular-nums tracking-tight text-slate-100">{value}</span><span className="text-xs text-slate-400">{suffix}</span></div><p className="mt-2 truncate text-[11px] text-slate-500">{detail}</p></div>
}

function SettingsPanel({ settings, onSave, onClose, busy }) {
  const [form, setForm] = useState({ daily_target: settings.daily_target || 3, target_duration_seconds: settings.target_duration_seconds || 150, schedule_enabled: Boolean(settings.schedule_enabled), schedule_time: settings.schedule_time || '00:30', publish_enabled: Boolean(settings.publish_enabled), publish_times: settings.publish_times || [], auto_publish_xiaohongshu: Boolean(settings.auto_publish_xiaohongshu), visual_style: settings.visual_style || 'tech_montage' })
  return (
    <form onSubmit={e => { e.preventDefault(); onSave({ ...form, auto_publish_xiaohongshu: form.publish_enabled ? false : form.auto_publish_xiaohongshu }) }} className="factory-enter mb-6 rounded-2xl border border-emerald-300/15 bg-[#101e24] p-5 sm:p-6">
      <div className="mb-5 flex items-center justify-between"><div><h3 className="text-sm font-semibold">生产计划</h3><p className="mt-1 text-xs text-slate-400">新设置用于后续生产批次，正在执行的任务按原计划继续。</p></div><button type="button" onClick={onClose} className="rounded-lg p-2 text-slate-400 hover:text-white" aria-label="关闭生产设置"><X size={18} /></button></div>
      <div className="flex flex-col gap-5 sm:flex-row sm:flex-wrap sm:items-end">
        <label className="text-xs text-slate-400">每日原创目标<div className="mt-2 flex items-center gap-2"><input type="number" min="1" max="10" required value={form.daily_target} onChange={e => setForm({ ...form, daily_target: e.target.value === '' ? '' : Number(e.target.value) })} className="w-24 rounded-lg border border-white/10 bg-slate-950/50 px-3 py-2.5 text-sm text-white" /><span>条</span></div></label>
        <label className="text-xs text-slate-400">每条目标时长（秒）<input aria-label="视频目标时长" type="number" min="60" max="180" required value={form.target_duration_seconds} onChange={e => setForm({ ...form, target_duration_seconds: Number(e.target.value) })} className="mt-2 block w-28 rounded-lg border border-white/10 bg-slate-950/50 px-3 py-2.5 text-sm text-white" /><span className="mt-2 block text-[11px] text-slate-500">150秒约2分30秒，成片允许±15秒</span></label>
        <label className="text-xs text-slate-400">每日提前开工（北京时间）<input type="time" required value={form.schedule_time} onChange={e => setForm({ ...form, schedule_time: e.target.value })} className="mt-2 block rounded-lg border border-white/10 bg-slate-950/50 px-3 py-2.5 text-sm text-white [color-scheme:dark]" /></label>
        <label className="text-xs text-slate-400">视频视觉风格<select value={form.visual_style} onChange={e => setForm({ ...form, visual_style: e.target.value })} className="mt-2 block w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-2.5 text-sm text-white"><option value="tech_montage">科技演示混剪（推荐）</option><option value="cards">文字卡片（旧版）</option></select><span className="mt-2 block max-w-64 text-[11px] leading-relaxed text-slate-500">{form.visual_style === 'tech_montage' ? '使用真实视频素材；素材不足时暂停该条制作并提示。' : '使用文字卡片与配音制作。'}</span></label>
        <label className="flex cursor-pointer items-center gap-3 py-3 text-sm text-slate-300"><input type="checkbox" checked={form.schedule_enabled} onChange={e => setForm({ ...form, schedule_enabled: e.target.checked })} className="h-4 w-4 accent-emerald-300" />启用每日自动生产</label>
        <label className="flex cursor-pointer items-start gap-3 py-2 text-sm text-slate-300"><input type="checkbox" checked={form.publish_enabled} onChange={e => setForm({ ...form, publish_enabled: e.target.checked })} className="mt-0.5 h-4 w-4 shrink-0 accent-emerald-300" /><span>到点自动提交<span className="mt-1 block max-w-64 text-[11px] leading-relaxed text-slate-500">提前 20 分钟上传，到点提交；超过 15 分钟未就绪则保留作品，不集中补发。需账号登录且发布能力可用。</span></span></label>
        <label className="w-full text-xs text-slate-400">发布时间（北京时间，每行或逗号分隔，每个时间对应一条原创）<input aria-label="每日发布时间" defaultValue={form.publish_times.join('，')} onChange={e => setForm({ ...form, publish_times: e.target.value.split(/[,，\s]+/).filter(Boolean) })} className="mt-2 block w-full rounded-lg border border-white/10 bg-slate-950/50 px-3 py-2.5 text-sm text-white" /></label>
        <button disabled={busy} className="inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-300 px-5 py-2.5 text-sm font-semibold text-slate-950 disabled:opacity-40 sm:ml-auto">{busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}保存计划</button>
      </div>
    </form>
  )
}

function ContentCard({ item, index, platformStates }) {
  const [expanded, setExpanded] = useState(false)
  const sources = Array.isArray(item.sources) ? item.sources : []
  const packages = Array.isArray(item.packages) ? item.packages : []
  const review = item.review || {}
  const issues = Array.isArray(review.issues) ? review.issues : []
  const preview = item.preview
  const delivery = (
    <>
      <div className="flex flex-col gap-5 p-4 sm:flex-row sm:p-5">
        <div className="relative flex aspect-[9/12] w-full shrink-0 items-center justify-center overflow-hidden rounded-lg border border-white/[0.04] bg-[#070c12] sm:w-36">
          {item.video_url ? <video key={`${item.video_url}|${item.webm_url || ''}`} poster={safeLink(item.cover_url)} controls playsInline preload="metadata" className="h-full max-h-80 w-full object-contain"><source src={safeLink(item.video_url)} type="video/mp4" />{item.webm_url && <source src={safeLink(item.webm_url)} type="video/webm" />}</video> : item.cover_url ? <img src={safeLink(item.cover_url)} alt={`${item.title || '视频'}封面`} className="h-full w-full object-cover" /> : <div className="px-3 text-center"><Film size={25} className="mx-auto mb-3 text-slate-700" /><span className="text-[10px] text-slate-500">{item.status === 'rendering' ? '视频制作中' : '等待生成画面'}</span></div>}
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-2"><span className="text-[10px] font-medium tracking-widest text-slate-500">CONTENT {String(index + 1).padStart(2, '0')}</span>{item.category && <span className="rounded bg-white/5 px-2 py-0.5 text-[10px] text-slate-400">{item.category}</span>}<Status value={item.status} small /></div>
          <h4 className="text-base font-semibold leading-relaxed text-slate-200">{item.title || '选题准备中'}</h4>{item.editor_name && <p className="mt-1 text-xs text-sky-200/70">剪辑负责人：{item.editor_name}</p>}
          {item.hook && <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-slate-400">{item.hook}</p>}
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px] text-slate-500">{item.duration > 0 && <span className="flex items-center gap-1"><Clock3 size={12} />{Math.round(item.duration)} 秒</span>}{VISUAL_STYLES[item.visual_style] && <span className="flex items-center gap-1"><Film size={12} />{VISUAL_STYLES[item.visual_style]}</span>}{VOICE_LABELS[item.tts_engine] && <span className="flex items-center gap-1"><Radio size={12} />{VOICE_LABELS[item.tts_engine]}</span>}<span className="flex items-center gap-1"><BookOpen size={12} />{sources.length} 个来源</span><span className="flex items-center gap-1"><PackageCheck size={12} />{packages.filter(p => p.download_url).length} 个平台素材包</span></div>
          {item.error && <p className="mt-3 break-words rounded-lg bg-rose-400/5 p-3 text-xs leading-relaxed text-rose-300">{item.error}</p>}
          <div className="mt-4 flex flex-wrap gap-2">
            {item.video_url && <a href={safeLink(item.video_url)} download className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-300/10 px-3 py-2 text-xs font-medium text-emerald-300 hover:bg-emerald-300/20"><ArrowDownToLine size={13} />下载成片</a>}
            <button onClick={() => setExpanded(!expanded)} className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.08] px-3 py-2 text-xs text-slate-400 hover:bg-white/5"><FileText size={13} />文案与依据<ChevronDown size={12} className={expanded ? 'rotate-180' : ''} /></button>
          </div>
        </div>
      </div>
      {packages.length > 0 && <div className="grid grid-cols-2 gap-px border-t border-white/[0.05] bg-white/[0.04] lg:grid-cols-4">{packages.map(pkg => <div key={pkg.platform} className="min-w-0 bg-[#0e1722] p-3"><div className="mb-2 flex items-center justify-between gap-1"><span className="text-xs font-medium text-slate-300">{PLATFORM_NAMES[pkg.platform] || pkg.platform}</span>{pkg.download_url && <a href={safeLink(pkg.download_url)} download aria-label={`下载${PLATFORM_NAMES[pkg.platform] || pkg.platform}素材包`} title="下载素材包" className="rounded p-1 text-emerald-300 hover:bg-emerald-300/10"><ArrowDownToLine size={14} /></a>}</div><p title={pkg.title} className="mb-2 truncate text-[11px] text-slate-400">{pkg.title || '标题准备中'}</p><Status value={pkg.status} small />{item.status === 'completed' && (pkg.platform === 'xiaohongshu' ? <XiaohongshuPublisher itemId={item.id} initialJob={item.platform_jobs?.xiaohongshu || item.xiaohongshu_job} publicationJobId={pkg.publication_job_id} packageStatus={pkg.status} /> : ['douyin', 'kuaishou'].includes(pkg.platform) ? <PlatformPublisher itemId={item.id} platform={pkg.platform} platformName={PLATFORM_NAMES[pkg.platform]} initialJob={item.platform_jobs?.[pkg.platform]} publicationJobId={pkg.publication_job_id} packageStatus={pkg.status} connectionState={platformStates[pkg.platform]} /> : null)}</div>)}</div>}
      {expanded && <div className="factory-enter space-y-5 border-t border-white/[0.06] p-5">
        <div><h5 className="mb-2 text-xs font-medium text-slate-400">原创口播稿</h5><p className="whitespace-pre-wrap break-words text-sm leading-7 text-slate-300">{item.script || '编剧尚未提交文案。'}</p></div>
        {sources.length > 0 && <div><h5 className="mb-2 text-xs font-medium text-slate-400">资料来源</h5><div className="space-y-2">{sources.map((source, i) => { const url = typeof source === 'string' ? source : source.url; return <a key={`${url}-${i}`} href={safeLink(url)} target="_blank" rel="noreferrer" className="flex items-start gap-2 break-all text-xs leading-relaxed text-sky-300/75 hover:text-sky-200"><ExternalLink size={12} className="mt-0.5 shrink-0" />{typeof source === 'string' ? source : source.title || source.name || source.url}{source.published_at && <span className="shrink-0 text-slate-500">{formatDate(source.published_at)}</span>}</a> })}</div></div>}
        {(review.summary || review.reason || issues.length > 0 || review.score != null) && <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3"><h5 className="mb-2 flex items-center gap-2 text-xs text-slate-400"><ShieldCheck size={14} />审核意见{review.score != null && <span className="ml-auto text-emerald-300">{asText(review.score)} 分</span>}</h5>{(review.summary || review.reason) && <p className="text-xs leading-relaxed text-slate-400">{asText(review.summary || review.reason)}</p>}{issues.map((issue, i) => <p key={i} className="mt-1 text-xs text-amber-300/80">{asText(issue)}</p>)}</div>}
        {packages.length > 0 && <details className="text-xs"><summary className="cursor-pointer text-slate-400">查看各平台发布文案</summary><div className="mt-3 grid gap-3 sm:grid-cols-2">{packages.map(pkg => <div key={pkg.platform} className="rounded-lg bg-white/[0.025] p-3"><p className="mb-2 text-slate-300">{PLATFORM_NAMES[pkg.platform] || pkg.platform} · {pkg.title}</p><p className="whitespace-pre-wrap break-words leading-relaxed text-slate-400">{pkg.caption}</p>{Array.isArray(pkg.tags) && <p className="mt-2 text-sky-300/70">{pkg.tags.map(tag => `#${tag}`).join(' ')}</p>}</div>)}</div></details>}
      </div>}
    </>
  )
  return (
    <article className="overflow-hidden rounded-xl border border-white/[0.07] bg-[#0c141e]">
      {preview && <section aria-label="新版演示混剪样片" className="border-b border-sky-300/15 bg-gradient-to-br from-sky-300/[0.07] to-transparent p-4 sm:p-5">
        <div className="mb-4 flex flex-wrap items-center gap-2"><Sparkles size={15} className="text-sky-300" /><span className="text-xs font-semibold text-sky-200">{preview.quality?.paper_evidence ? '小影2 · 真实论文讲解样片' : '新版演示混剪样片'}{preview.published === false ? ' · 未发布' : ''}</span></div>
        <div className="flex flex-col gap-5 sm:flex-row">
          <div className="flex aspect-[9/16] w-full shrink-0 items-center justify-center overflow-hidden rounded-xl border border-white/10 bg-[#050a10] sm:w-48">
            {preview.video_url ? <video key={`${preview.video_url}|${preview.webm_url || ''}`} poster={safeLink(preview.cover_url)} controls playsInline preload="metadata" aria-label={`${item.title || '视频'}新版演示混剪样片`} className="h-full max-h-[420px] w-full object-contain"><source src={safeLink(preview.video_url)} type="video/mp4" />{preview.webm_url && <source src={safeLink(preview.webm_url)} type="video/webm" />}</video> : <div className="text-center text-xs text-slate-500"><Film size={24} className="mx-auto mb-2" />样片准备中</div>}
          </div>
          <div className="min-w-0 flex-1 py-1"><div className="mb-2 text-[10px] font-medium tracking-widest text-slate-500">PREVIEW {String(index + 1).padStart(2, '0')}</div><h4 className="text-base font-semibold leading-relaxed text-slate-100">{preview.title || item.title || '新版样片'}</h4>{item.hook && <p className="mt-3 text-xs leading-relaxed text-slate-400">{item.hook}</p>}<div className="mt-4 flex flex-wrap gap-3 text-xs text-slate-400"><span className="flex items-center gap-1.5"><Film size={13} />{VISUAL_STYLES[preview.style] || '视频样片'}</span>{preview.duration > 0 && <span className="flex items-center gap-1.5"><Clock3 size={13} />{Math.round(preview.duration)} 秒</span>}</div>{preview.quality?.paper_evidence && <p className="mt-3 text-xs leading-6 text-sky-200">展示真实论文第 {preview.quality.paper_evidence.pages.join('、')} 页，口播对应原文高亮。<a className="ml-2 underline" href={`https://arxiv.org/abs/${encodeURIComponent(preview.quality.paper_evidence.paper_id)}`} target="_blank" rel="noreferrer">查看原论文 ↗</a></p>}{preview.quality?.source_uniqueness_passed && <p className="mt-2 text-xs text-emerald-300">源片段无重复使用 · 已检查重复画面</p>}{preview.published === false && <p className="mt-4 rounded-lg border border-amber-300/10 bg-amber-300/[0.03] p-3 text-xs leading-relaxed text-amber-200/80">此版本供观看与验收，尚未提交平台。</p>}{preview.video_url && <a href={safeLink(preview.video_url)} download className="mt-4 inline-flex items-center gap-2 rounded-lg bg-sky-300/10 px-3 py-2.5 text-xs font-medium text-sky-200 hover:bg-sky-300/20"><ArrowDownToLine size={14} />下载新版样片</a>}</div>
        </div>
      </section>}
      {preview ? <details className="group/delivery"><summary className="flex cursor-pointer list-none items-center gap-2 bg-white/[0.02] px-5 py-4 text-xs text-slate-400"><ChevronRight size={14} className="transition-transform group-open/delivery:rotate-90" /><span className="font-medium text-slate-300">当前交付版本</span><span className="ml-auto text-[10px] text-slate-500">原视频与平台记录</span></summary>{delivery}</details> : delivery}
    </article>
  )
}

function RunReport({ report }) {
  if (!report || Object.keys(report).length === 0) return null
  const production = report.production || {}
  const suggestions = report.recommendations || report.suggestions || report.next_steps || []
  return <div className="rounded-xl border border-emerald-300/10 bg-emerald-300/[0.025] p-5"><div className="mb-3 flex items-center gap-2 text-sm font-medium text-emerald-200"><TrendingUp size={16} />负责人日报</div>{Object.keys(production).length > 0 && <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">{[{ label: '成片交付', value: `${production.completed ?? 0}/${production.target ?? 0}` }, { label: '平台素材包', value: production.packages ?? 0 }, { label: '已提交平台', value: production.submitted ?? 0 }, { label: '公开发布', value: production.published ?? 0 }, { label: '待处理内容', value: production.failed ?? 0 }].map(metric => <div key={metric.label} className="rounded-lg bg-white/[0.025] p-3"><p className="text-[10px] text-slate-400">{metric.label}</p><p className="mt-1 text-lg font-semibold tabular-nums text-slate-300">{metric.value}</p></div>)}</div>}<p className="whitespace-pre-wrap break-words text-sm leading-7 text-slate-300">{asText(report.summary || report.conclusion || report.message || '本轮生产记录已归档。')}</p>{Array.isArray(suggestions) && suggestions.length > 0 && <ul className="mt-3 space-y-2">{suggestions.map((suggestion, i) => <li key={i} className="flex gap-2 text-xs leading-relaxed text-slate-400"><ArrowRight size={12} className="mt-0.5 shrink-0 text-emerald-300/60" />{asText(suggestion)}</li>)}</ul>}{report.platform_data_available === false && <p className="mt-4 text-[11px] text-slate-500">发布平台数据尚未接入，本次复盘基于生产与质量记录。</p>}</div>
}

export default function FactoryDashboard() {
  const [overview, setOverview] = useState(null)
  const [platformStates, setPlatformStates] = useState({})
  const updatePlatformState = useCallback((platform, state) => {
    setPlatformStates(current => ({ ...current, [platform]: state }))
  }, [])
  const [selectedId, setSelectedId] = useState(null)
  const [run, setRun] = useState(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState('')
  const [detailError, setDetailError] = useState('')
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [showSettings, setShowSettings] = useState(false)
  const [tab, setTab] = useState('content')
  const mounted = useRef(true)
  const selectedRef = useRef(null)
  selectedRef.current = selectedId

  async function refreshOverview() {
    const data = await factoryApi.overview()
    if (!mounted.current) return
    setOverview(data)
    setSelectedId(current => current || data.runs?.[0]?.id || null)
    setError('')
    return data
  }
  async function refreshDetail(id) {
    if (!id) return
    const data = await factoryApi.getRun(id)
    if (!mounted.current || selectedRef.current !== id) return
    setRun(data)
    setDetailError('')
  }
  useEffect(() => {
    mounted.current = true
    let pending = false
    const poll = async () => {
      if (pending) return
      pending = true
      try { await refreshOverview() } catch (e) { if (mounted.current) setError(e.message) } finally { pending = false; if (mounted.current) setLoading(false) }
    }
    poll()
    const timer = setInterval(poll, 5000)
    return () => { mounted.current = false; clearInterval(timer) }
  }, [])
  useEffect(() => {
    if (!selectedId) { setRun(null); return }
    let active = true
    let pending = false
    setRun(null)
    setDetailLoading(true)
    setDetailError('')
    const poll = async () => {
      if (pending) return
      pending = true
      try {
        const data = await factoryApi.getRun(selectedId)
        if (active) { setRun(data); setDetailError('') }
      } catch (e) { if (active) setDetailError(e.message) } finally { pending = false; if (active) setDetailLoading(false) }
    }
    poll()
    const timer = setInterval(poll, 3000)
    return () => { active = false; clearInterval(timer) }
  }, [selectedId])
  useEffect(() => {
    if (!notice) return
    const timer = setTimeout(() => setNotice(''), 6000)
    return () => clearTimeout(timer)
  }, [notice])

  async function perform(key, action, message) {
    setBusy(key)
    setActionError('')
    try {
      const response = await action()
      await refreshOverview()
      if (key === 'create' || key === 'retry') {
        const id = response.id || response.run_id || response.run?.id
        if (id) { setSelectedId(id); setTab('activity'); await refreshDetail(id) }
      } else if (selectedId) await refreshDetail(selectedId)
      setNotice(response.status === 'paused_memory' ? '内存保护已暂停本轮，进度已保留。' : message)
      if (key === 'settings') setShowSettings(false)
    } catch (e) { setActionError(e.message) } finally { setBusy('') }
  }

  const settings = overview?.settings || {}
  const agents = overview?.agents || []
  const runs = overview?.runs || []
  const platforms = overview?.platforms || []
  const items = run?.items || []
  const events = [...(run?.events || [])].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
  const target = settings.daily_target || 3
  const configuredPlatforms = Array.isArray(settings.platforms) ? settings.platforms.length : platforms.length
  const hasActiveRun = runs.some(r => ACTIVE.has(r.status))
  const hasMemoryPaused = runs.some(r => r.status === 'paused_memory')
  const memory = overview?.runtime?.memory
  const otherMemoryPaused = runs.some(r => r.status === 'paused_memory' && r.id !== run?.id)
  const completeCount = run?.completed_count || 0
  const runTarget = run?.target_count || target
  const completion = Math.min(100, Math.round(completeCount / Math.max(runTarget, 1) * 100))
  const stageName = ['model_retry', 'model_call'].includes(run?.stage)
    ? events.find(event => STAGES.some(stage => stage.keys.includes(event.stage)))?.stage
    : run?.stage || run?.status
  const activeStage = STAGES.findIndex(stage => stage.keys.includes(stageName))
  const queued = overview?.runtime?.queue_available
  const displayedError = error || actionError
  const stoppedWithoutContent = run && !ACTIVE.has(run.status)

  return (
    <div className="factory-enter mx-auto max-w-[1550px] space-y-7 pb-8">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] pb-5"><div className="flex items-center gap-2 text-[11px] text-slate-400"><span className="text-slate-300">工作空间</span><ChevronRight size={12} /><span>工厂工作台</span></div><div className="flex items-center gap-4 text-[11px]"><span className="text-slate-500">北京时间</span><span className={`flex items-center gap-1.5 ${error || queued === false ? 'text-amber-300' : overview ? 'text-emerald-300/80' : 'text-slate-400'}`}><span className={`h-1.5 w-1.5 rounded-full ${error || queued === false ? 'bg-amber-300' : overview ? 'bg-emerald-300' : 'bg-slate-600'}`} />{error ? '连接中断，自动重连' : queued === false ? '任务队列未连接' : overview ? '工厂服务已连接' : '连接工厂服务'}</span></div></header>

      <section className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
        <div><div className="mb-3 flex items-center gap-2 text-[10px] font-medium tracking-[0.18em] text-emerald-300/70"><span className="h-px w-5 bg-emerald-300/50" />YOUR AI CONTENT TEAM</div><h2 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">创意开工，内容持续发生。</h2><p className="mt-3 text-sm leading-relaxed text-slate-400">{settings.sector || 'AI 科技'} · 每天 {target} 条原创 · {VISUAL_STYLES[settings.visual_style] || '科技演示混剪'}</p></div>
        <div className="flex shrink-0 gap-2"><button onClick={() => setShowSettings(!showSettings)} disabled={!overview} className="inline-flex items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.025] px-4 py-3 text-xs font-medium text-slate-300 hover:bg-white/5 disabled:opacity-40"><Settings2 size={15} />生产计划</button><button onClick={() => perform('create', () => factoryApi.createRun(target), '生产任务已提交，团队开始协作。')} disabled={Boolean(busy) || !overview || hasActiveRun || hasMemoryPaused || memory?.blocked || queued === false} className="inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-300 px-5 py-3 text-xs font-bold text-slate-950 shadow-[0_4px_20px_#6ee7b712] transition hover:bg-emerald-200 disabled:opacity-40">{busy === 'create' ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} fill="currentColor" />}{hasMemoryPaused || memory?.blocked ? '内存保护已暂停' : hasActiveRun ? '团队正在生产' : '启动一轮生产'}</button></div>
      </section>

      {(displayedError || notice) && <div role={displayedError ? 'alert' : 'status'} className={`flex items-start gap-3 rounded-xl border p-4 text-xs leading-relaxed ${displayedError ? 'border-rose-400/20 bg-rose-400/5 text-rose-300' : 'border-emerald-300/15 bg-emerald-300/5 text-emerald-200'}`}><AlertCircle size={16} className="shrink-0" /><span className="min-w-0 flex-1 break-words">{displayedError || notice}</span>{error && <button onClick={() => refreshOverview().catch(e => setError(e.message))} className="shrink-0 underline">重新连接</button>}<button onClick={() => { setError(''); setActionError(''); setNotice('') }} aria-label="关闭提示"><X size={14} /></button></div>}
      {showSettings && overview && <SettingsPanel settings={settings} busy={busy === 'settings'} onClose={() => setShowSettings(false)} onSave={form => perform('settings', () => factoryApi.updateSettings(form), '生产计划已更新。')} />}

      {memory && <div aria-label="内存保护状态" className={`rounded-xl border px-4 py-3 text-xs leading-relaxed ${memory.blocked || hasMemoryPaused ? 'border-amber-300/20 bg-amber-300/5 text-amber-200' : 'border-white/[0.06] text-slate-400'}`}>
        <p className="flex flex-wrap items-center gap-x-5 gap-y-1"><span className="font-medium">{memory.blocked ? '内存保护：暂停新任务' : hasMemoryPaused ? '内存已恢复，等待手动恢复批次' : '内存正常'}</span>{memory.project_gib != null && <span>工程 {memory.project_gib} GB / 暂停阈值 {memory.max_project_gib} GB</span>}{memory.available_gib != null && <span>机器可用 {memory.available_gib} GB</span>}</p>
        {(memory.blocked || hasMemoryPaused) && <p className="mt-1">{memory.blocked ? memory.reason + '。' : ''}已完成的视频和进度会保留；内存恢复后，在暂停批次中点击“恢复本轮”。</p>}
      </div>}

      <section className="grid grid-cols-2 gap-3 xl:grid-cols-4"><Stat icon={Film} label="每日原创目标" value={overview ? target : '—'} suffix="条 / 天" detail="从选题到通过质检的完整成片" /><Stat icon={Bot} label="AI 员工团队" value={overview ? agents.length : '—'} suffix="个岗位" detail="负责人统筹，专业员工逐步交接" /><Stat icon={Layers3} label="平台适配" value={overview ? configuredPlatforms : '—'} suffix="个平台" detail={platforms.filter(platform => platform.enabled !== false).map(platform => platform.name || PLATFORM_NAMES[platform.id] || platform.id).join(' · ')} /><Stat icon={Clock3} label="每日自动开工" value={overview ? settings.schedule_enabled ? settings.schedule_time || '08:00' : '已暂停' : '—'} suffix={settings.schedule_enabled ? '北京时间' : ''} detail={settings.schedule_enabled ? `每日生成 ${target} 条，最多 ${target * configuredPlatforms} 个发布素材包` : '可在生产计划中启用定时任务'} /></section>

      <FactoryOperations analytics={overview?.analytics} operations={overview?.operations} library={overview?.asset_library} />
      {overview?.strategy && <section className="rounded-2xl border border-emerald-300/20 bg-emerald-300/[0.04] p-5">
        <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-xs text-emerald-300">老周的共同目标 · {overview.strategy.positioning}</p><h3 className="mt-2 text-lg font-semibold text-slate-100">{overview.strategy.objective}</h3><p className="mt-2 text-sm text-slate-400">{overview.strategy.audience_promise}</p></div><div className="text-xs leading-6 text-slate-400">素材库 · {overview.asset_library?.videos || 0} 段动态素材<br />{overview.asset_library?.ready_source_pages || 0} 个来源具备混剪素材</div></div>
        <div className="mt-4 flex flex-wrap gap-2">{overview.strategy.series.map(name => <span key={name} className="rounded-full border border-white/10 px-3 py-1 text-xs text-slate-300">{name}</span>)}</div>
        <p className="mt-4 text-xs leading-6 text-slate-400">观察指标：{[...overview.strategy.primary_metrics, ...overview.strategy.diagnostic_metrics].join(' · ')}<br />{overview.strategy.measurement_note}</p>
      </section>}
      <section><div className="mb-4 flex items-center justify-between"><div className="flex items-center gap-2"><Bot size={17} className="text-emerald-300/70" /><h3 className="text-sm font-semibold text-slate-200">你的 AI 团队</h3></div><span className="text-[10px] text-slate-500">任务驱动 · 状态实时更新</span></div><div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-3">{loading && !overview ? Array.from({ length: 9 }, (_, i) => <div key={i} className="h-40 animate-pulse rounded-xl border border-white/[0.04] bg-white/[0.025]" />) : agents.map(agent => { const Icon = AGENT_ICONS[agent.id] || Bot; const working = ['active', 'busy', 'working', 'running'].includes(agent.status); return <div key={agent.id} className={`relative rounded-xl border p-4 transition-colors ${working ? 'border-emerald-300/25 bg-emerald-300/[0.04]' : 'border-white/[0.06] bg-[#0d151f]'}`}><div className="mb-4 flex items-center justify-between"><div className={`rounded-lg p-2 ${agent.id === 'director' ? 'bg-emerald-300/10 text-emerald-300' : 'bg-white/[0.04] text-slate-400'}`}><Icon size={17} /></div><span className={`h-1.5 w-1.5 rounded-full ${working ? 'animate-pulse bg-emerald-300' : agent.status === 'error' ? 'bg-rose-300' : 'bg-slate-600'}`} title={STATUS_LABELS[agent.status] || agent.status} /></div><p className="text-sm font-semibold text-slate-200">{agent.name || agent.role}</p><p className="mt-1 text-[10px] text-emerald-200/70">{agent.role}</p><p className="mt-1.5 min-h-16 text-[10px] leading-relaxed text-slate-400" title={agent.description}>{asText(agent.current_task || agent.description || agent.role)}</p><div className="mt-3"><Status value={agent.status || 'idle'} small /></div></div>})}</div></section>

      {settings.publish_enabled && <section className="mb-6 rounded-2xl border border-white/[0.07] bg-[#0d141e] p-5"><div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold text-slate-200">小麦的发布排期 · 北京时间</h3><span className="text-xs text-slate-400">每天 {target} 条原创 · 最多 {target * configuredPlatforms} 次提交</span></div><p className="mb-4 text-xs leading-relaxed text-slate-400">{(settings.publish_times || []).join(' · ')}。B站预留长视频，当前暂停。实际提交以各平台任务回执为准。</p><div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">{(overview?.releases || []).slice(0, 20).map(slot => <div key={slot.id} className="rounded-xl border border-white/[0.06] p-3"><div className="flex items-center justify-between gap-1"><span className="text-xs text-slate-200">{formatDate(slot.due_at)}</span><Status value={slot.status} small /></div><p className="mt-2 truncate text-[11px] text-slate-400" title={slot.title}>{slot.title || '等待原创成片'}</p>{slot.platforms.map(platform => <p key={platform} className="mt-1 text-[10px] leading-relaxed text-slate-500" title={slot.results?.[platform]?.message}>{PLATFORM_NAMES[platform]}：{slot.results?.[platform]?.message || '等待排期'}</p>)}</div>)}</div></section>}

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
        <section className="min-w-0 overflow-hidden rounded-2xl border border-white/[0.07] bg-[#0d141e]">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] px-5 py-4"><div className="flex items-center gap-2"><Activity size={16} className="text-emerald-300/70" /><h3 className="text-sm font-semibold">生产现场</h3>{run && <Status value={run.cancel_requested && ACTIVE.has(run.status) ? 'cancel_requested' : run.status} small />}</div><div className="flex items-center gap-2">{run && (ACTIVE.has(run.status) || run.status === 'paused_memory') && <button onClick={() => perform('cancel', () => factoryApi.cancelRun(run.id), '已请求取消，当前步骤结束后停止后续生产。')} disabled={Boolean(busy) || run.cancel_requested || ['cancel_requested', 'cancelling'].includes(run.status)} className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-[11px] text-slate-400 hover:text-white disabled:opacity-40"><Pause size={12} />{run.cancel_requested ? '正在停止' : '停止本轮'}</button>}{run && RETRYABLE.has(run.status) && <button onClick={() => perform('retry', () => factoryApi.retryRun(run.id), run.status === 'paused_memory' ? '已从保存的进度恢复本轮。' : '重试任务已提交。')} disabled={Boolean(busy) || hasActiveRun || otherMemoryPaused || memory?.blocked || queued === false} className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-300/10 px-2.5 py-1.5 text-[11px] text-emerald-300 disabled:opacity-40"><RefreshCw size={12} />{run.status === 'paused_memory' ? '恢复本轮' : '重试未完成内容'}</button>}</div></div>
          {detailError && <p role="alert" className="m-5 rounded-lg bg-rose-400/5 p-3 text-xs text-rose-300">{detailError}</p>}
          {detailLoading ? <div className="flex items-center justify-center gap-2 py-24 text-xs text-slate-400"><Loader2 size={18} className="animate-spin" />读取生产记录…</div> : !run ? <Empty icon={Workflow} title="团队已准备好，等待第一轮生产" description="点击“启动一轮生产”，查看各岗位如何协作，把 AI 科技选题制作成原创视频。" /> : <>
            <div className="px-5 pt-5"><div className="mb-3 flex items-center justify-between text-[11px]"><span className="text-slate-400">{formatDate(run.created_at)} · {run.trigger === 'scheduled' || run.trigger === 'schedule' ? '定时生产' : '手动生产'}</span><span className="text-slate-400"><strong className="font-semibold text-emerald-300">{completeCount}</strong> / {runTarget} 条已完成</span></div><div className="h-1 overflow-hidden rounded-full bg-white/[0.06]"><div className="h-full rounded-full bg-emerald-300 transition-all duration-700" style={{ width: `${completion}%` }} /></div>
              <div className="my-5 grid grid-cols-3 gap-3 sm:grid-cols-6">{STAGES.map(({ label, icon: Icon }, i) => { const finished = run.status === 'completed' || (activeStage >= 0 && i < activeStage); const active = i === activeStage && ACTIVE.has(run.status); return <div key={label} className={`flex flex-col items-center gap-2 text-center ${finished ? 'text-emerald-300/75' : active ? 'text-sky-300' : 'text-slate-500'}`}><div className={`rounded-lg border p-2 ${active ? 'border-sky-300/25 bg-sky-300/5' : finished ? 'border-emerald-300/10 bg-emerald-300/5' : 'border-white/[0.04]'}`}>{finished ? <Check size={14} /> : <Icon size={14} className={active ? 'animate-pulse' : ''} />}</div><span className="text-[10px]">{label}</span></div> })}</div>
              {run.error && <div className="mb-4 flex gap-2 rounded-lg bg-rose-400/5 p-3 text-xs leading-relaxed text-rose-300"><AlertCircle size={14} className="mt-0.5 shrink-0" /><p className="break-words">{run.error}</p></div>}
              <div className="flex gap-6 border-b border-white/[0.06]" role="tablist" aria-label="生产记录视图">{[{ id: 'content', label: '内容交付', count: items.length }, { id: 'activity', label: '协作记录', count: events.length }, { id: 'report', label: '生产日报' }].map(t => <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)} className={`relative pb-3 text-xs transition ${tab === t.id ? 'text-emerald-300' : 'text-slate-400 hover:text-slate-300'}`}>{t.label}{t.count != null && <span className="ml-1.5 text-[10px] opacity-60">{t.count}</span>}{tab === t.id && <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-emerald-300" />}</button>)}</div>
            </div>
            <div className="p-5" role="tabpanel">{tab === 'content' && (items.length ? <div className="space-y-4">{items.map((item, i) => <ContentCard key={item.id} item={item} index={i} platformStates={platformStates} />)}</div> : <Empty icon={FileText} title={stoppedWithoutContent ? '本轮尚未产出内容' : '选题正在酝酿'} description={stoppedWithoutContent ? '可在协作记录中查看中断原因，处理后重试本轮任务。' : '情报员和负责人完成资料筛选后，原创内容将在这里出现。'} />)}
              {tab === 'activity' && (events.length ? <div className="space-y-0">{events.slice(0, 80).map((event, i) => { const Icon = AGENT_ICONS[event.agent_id] || Bot; return <div key={event.id || `${i}-${event.created_at}`} className="relative flex gap-3 pb-5 last:pb-0">{i < Math.min(events.length, 80) - 1 && <span className="absolute bottom-0 left-[15px] top-8 w-px bg-white/[0.06]" />}<div className={`z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border ${statusColor(event.status)}`}><Icon size={13} /></div><div className="min-w-0 flex-1 pt-0.5"><div className="flex flex-wrap items-center gap-2"><span className="text-xs font-medium text-slate-300">{event.agent_name || agents.find(a => a.id === event.agent_id)?.name || '工厂服务'}</span><span className="text-[10px] text-slate-500">{formatDate(event.created_at, true)}</span><span className="ml-auto text-[10px] text-slate-500">{STATUS_LABELS[event.status] || ''}</span></div><p className="mt-1.5 break-words text-xs leading-relaxed text-slate-400">{event.message}</p></div></div> })}</div> : <Empty icon={Activity} title="等待任务开始" description="每一次任务交接、审核与制作结果都会记录在这里。" />)}
              {tab === 'report' && (run.report && Object.keys(run.report).length ? <RunReport report={run.report} /> : <Empty icon={TrendingUp} title={stoppedWithoutContent ? '本轮未生成日报' : '日报将在本轮结束后生成'} description={stoppedWithoutContent ? '任务尚未完成复盘步骤，请先查看协作记录与具体错误。' : '负责人汇总实际产量、质量检查和异常情况，给出下一轮改进建议。'} />)}
            </div>
          </>}
        </section>

        <aside className="space-y-5">
          <section className="overflow-hidden rounded-2xl border border-white/[0.07] bg-[#0d141e]"><div className="flex items-center gap-2 border-b border-white/[0.06] p-4"><History size={15} className="text-slate-400" /><h3 className="text-xs font-semibold text-slate-300">生产批次</h3><span className="ml-auto text-[10px] text-slate-500">最近 {runs.length} 轮</span></div>{runs.length ? <div className="max-h-80 space-y-1 overflow-y-auto p-2">{runs.map(r => <button key={r.id} onClick={() => setSelectedId(r.id)} className={`w-full rounded-xl border p-3 text-left transition ${selectedId === r.id ? 'border-emerald-300/15 bg-emerald-300/[0.04]' : 'border-transparent hover:bg-white/[0.025]'}`}><div className="mb-2 flex items-center justify-between gap-2"><span className={`text-xs ${selectedId === r.id ? 'text-emerald-200' : 'text-slate-400'}`}>{formatDate(r.created_at)}</span><Status value={r.status} small /></div><div className="flex items-center justify-between text-[10px] text-slate-500"><span>{r.trigger === 'scheduled' || r.trigger === 'schedule' ? '定时任务' : '手动任务'} · {r.completed_count || 0}/{r.target_count} 条</span><ChevronRight size={12} /></div></button>)}</div> : <p className="p-5 text-xs text-slate-500">暂无生产批次</p>}</section>
          <section className="rounded-2xl border border-white/[0.07] bg-[#0d141e] p-4"><div className="mb-4 flex items-center gap-2"><Radio size={15} className="text-slate-400" /><h3 className="text-xs font-semibold text-slate-300">发布通道</h3></div><div className="space-y-4">{platforms.map(platform => <PlatformConnection key={platform.id} platform={{ ...platform, name: platform.name || PLATFORM_NAMES[platform.id] }} statusBadge={<Status value={platform.status} small />} loadState={factoryApi.getPlatformLoginState} onStateChange={updatePlatformState} />)}</div><div className="mt-4 rounded-lg border border-amber-300/[0.08] bg-amber-300/[0.025] p-3 text-[10px] leading-relaxed text-slate-400">素材包就绪表示视频与发布文案已准备完成；实际发布结果会单独记录。</div></section>
          <div className="px-2 text-[10px] leading-relaxed text-slate-500"><div className="mb-2 flex items-center gap-1.5"><FlaskConical size={12} /><span>AI 科技内容生产</span></div><p>每条内容保留来源和审核记录。演示、实测与数据结论以实际证据为依据。</p></div>
        </aside>
      </div>
    </div>
  )
}
