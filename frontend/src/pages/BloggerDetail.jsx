import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, Plus, Mic, RefreshCw } from 'lucide-react'
import { api } from '../api/client'
import DnaVisualizer from '../components/DnaVisualizer'

export default function BloggerDetail() {
  const { id } = useParams()
  const [blogger, setBlogger] = useState(null)
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [videoUrl, setVideoUrl] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const importing = blogger?.videos?.some(v => ['pending', 'downloading', 'extracting', 'transcribing'].includes(v.status))
  const [actionLoading, setActionLoading] = useState('')

  useEffect(() => { loadBlogger() }, [id])
  const busy = importing || blogger?.status === 'analyzing' || ['waiting', 'checking', 'importing', 'analyzing'].includes(blogger?.watch?.status)
  const watching = blogger?.watch?.enabled
  useEffect(() => {
    if (!busy && !watching) return
    const timer = setInterval(loadBlogger, busy ? 4000 : 30000)
    return () => clearInterval(timer)
  }, [id, busy, watching])

  async function loadBlogger() {
    try {
      const res = await api.getBlogger(id)
      setBlogger(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleAddVideo(e) {
    e.preventDefault()
    setError('')
    setActionLoading('adding')
    try {
      const result = await api.addVideo(id, { source_url: videoUrl })
      setNotice(result.reused ? result.message : '已添加视频，正在下载和转写。')
      setShowAdd(false)
      setVideoUrl('')
      loadBlogger()
    } catch (e) {
      setError(e.message)
    } finally {
      setActionLoading('')
    }
  }

  async function handleDownload(videoId) {
    setActionLoading(`download-${videoId}`)
    try {
      // Trigger download + transcribe pipeline
      await api.retryVideo(videoId)
      setNotice('已重新安排导入，会保留已完成的视频和转写步骤。')
      await loadBlogger()
    } catch (e) {
      setError(e.message)
    } finally {
      setActionLoading('')
    }
  }

  async function handleAnalyze() {
    setActionLoading('analyze')
    try {
      await api.analyzeBlogger(id)
      setNotice('正在分析实际转写与可用画面，结果会自动更新。')
      await loadBlogger()
    } catch (e) {
      setError(e.message)
    } finally {
      setActionLoading('')
    }
  }

  async function handleWatch(refresh = false) {
    setActionLoading('watch'); setError('')
    try {
      if (refresh) await api.refreshBloggerWatch(id)
      else await api.updateBloggerWatch(id, !blogger.watch?.enabled)
      setNotice(refresh ? '已安排检查新作品，已有分析与成片会保留。' : blogger.watch?.enabled ? '已关闭自动跟踪。' : '已启用自动跟踪，每3小时检查，自动学习新样本。')
      await loadBlogger()
    } catch (e) { setError(e.message) }
    finally { setActionLoading('') }
  }

  const watchStatus = { disabled: '未开启', waiting: '待检查', checking: '正在检查主页', importing: '正在导入新样本', analyzing: '正在更新风格分析', up_to_date: '检查完成', partial: '部分样本未完成', blocked: '检查受阻', paused_memory: '内存保护暂停' }
  const watchDate = (value) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '尚无记录'

  const statusColors = {
    pending: 'bg-gray-500/20 text-gray-400',
    downloading: 'bg-yellow-500/20 text-yellow-400',
    downloaded: 'bg-blue-500/20 text-blue-400',
    transcribing: 'bg-yellow-500/20 text-yellow-400',
    transcribed: 'bg-green-500/20 text-green-400',
    error: 'bg-red-500/20 text-red-400',
    preview_only: 'bg-amber-500/20 text-amber-300',
  }

  if (loading) return <div className="text-gray-500 animate-pulse">加载中...</div>
  if (!blogger) return <div className="text-red-400">博主不存在</div>

  const hasTranscribableVideos = (blogger.videos || []).some(v => v.status === 'downloaded' || v.status === 'pending')
  const hasTranscripts = (blogger.videos || []).some(v => v.has_transcript)

  return (
    <div>
      <Link to="/bloggers" className="flex items-center gap-2 text-sm text-gray-400 hover:text-white mb-4 transition-colors">
        <ArrowLeft size={16} /> 返回博主列表
      </Link>

      {notice && <div role="status" className="mb-4 rounded-lg bg-green-500/10 p-3 text-sm text-green-300">{notice}</div>}
      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-400 p-3 rounded-lg mb-4 text-sm">{error}</div>}

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-4">
          <div className="w-16 h-16 rounded-full bg-gray-800 flex items-center justify-center text-2xl font-bold text-blue-400">
            {blogger.name[0]}
          </div>
          <div>
            <h2 className="text-2xl font-bold">{blogger.name}</h2>
            <p className="text-sm text-gray-400">{blogger.platform} · {blogger.status}</p>
          </div>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setShowAdd(true)}
            className="flex items-center gap-2 bg-gray-800 hover:bg-gray-700 px-4 py-2 rounded-lg text-sm transition-colors">
            <Plus size={16} /> 添加视频
          </button>
          <button onClick={handleAnalyze} disabled={!hasTranscripts || blogger.status === 'analyzing' || actionLoading === 'analyze'}
            className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed px-4 py-2 rounded-lg text-sm transition-colors">
            <RefreshCw size={16} className={actionLoading === 'analyze' ? 'animate-spin' : ''} />
            {blogger.content_dna ? '重新蒸馏' : '开始蒸馏分析'}
          </button>
        </div>
      </div>

      {blogger.platform === 'douyin' && <section className="mb-6 rounded-xl border border-gray-800 bg-gray-900 p-4" aria-label="自动跟踪对标新作品">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h3 className="font-semibold">自动跟踪新作品</h3><p className="mt-1 text-sm text-gray-400">每3小时检查公开主页；每轮最多学习3条新作品，每日最多6条，自动去重。</p></div>
          <div className="flex gap-2">
            <button disabled={actionLoading === 'watch'} onClick={() => handleWatch()} className="rounded-lg bg-gray-800 px-3 py-2 text-sm disabled:opacity-50">{blogger.watch?.enabled ? '关闭跟踪' : '开启自动跟踪'}</button>
            {blogger.watch?.enabled && <button disabled={actionLoading === 'watch' || ['checking', 'importing', 'analyzing'].includes(blogger.watch?.status)} onClick={() => handleWatch(true)} className="rounded-lg bg-blue-600 px-3 py-2 text-sm disabled:opacity-50">检查新作品</button>}
          </div>
        </div>
        <p className="mt-3 text-sm">状态：{watchStatus[blogger.watch?.status] || '未开启'} · 本轮发现 {blogger.watch?.summary?.discovered_count ?? '—'} 条，新增 {blogger.watch?.summary?.new_count ?? '—'} 条</p>
        <p className="mt-2 text-xs leading-6 text-gray-400">最近检查：{watchDate(blogger.watch?.last_checked_at)}　下次检查：{watchDate(blogger.watch?.next_check_at)}　分析更新：{watchDate(blogger.watch?.last_analyzed_at)}（北京时间）</p>
        {blogger.watch?.error_message && <p role="alert" className="mt-2 text-sm text-amber-300">{blogger.watch.error_message}</p>}
        <p className="mt-2 text-xs text-gray-500">学习开场、镜头、字幕和叙事方法，分析结果提供给小影、小影3和编导；样本仅供对标研究。验证受阻或没有完整视频时会保留上次分析，不计为已学习。</p>
        {blogger.watch?.summary?.latest_posts?.length > 0 && <details className="mt-3 text-sm text-gray-300"><summary className="cursor-pointer">本轮核对到的最新作品</summary><ul className="mt-2 space-y-2">{blogger.watch.summary.latest_posts.slice(0, 6).map(post => <li key={post.post_id}><a className="text-blue-300 hover:underline" href={post.source_url} target="_blank" rel="noreferrer">{post.title?.slice(0, 70) || post.post_id}</a><span className="ml-2 text-xs text-gray-500">{watchDate(post.published_at)}</span></li>)}</ul></details>}
      </section>}

      {blogger.platform === 'wechat_channels' && <section className="mb-6 rounded-xl border border-amber-800/50 bg-gray-900 p-4" aria-label="微信视频号接入状态">
        <h3 className="font-semibold">微信视频号 · {blogger.watch?.status === 'awaiting_feed' ? '已核对单条预览，持续追踪待接通' : '待接入'}</h3>
        <p className="mt-2 text-sm text-amber-300">{blogger.watch?.error_message || '需要首次提供可打开的本账号视频号作品分享链接，用于核对账号及测试访问方式。'} </p>
        <p className="mt-2 text-xs text-gray-400">单条预览文字与已下载音视频分别记录；抖音对标跟踪独立运行。</p>
      </section>}

      {/* Add video dialog */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-900 rounded-xl p-6 w-full max-w-md border border-gray-800">
            <h3 className="text-lg font-semibold mb-4">添加视频</h3>
            <form onSubmit={handleAddVideo} className="space-y-4">
              <div>
                <label className="block text-sm text-gray-400 mb-1">视频链接 *</label>
                <input required value={videoUrl} onChange={e => setVideoUrl(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
                  placeholder="支持粘贴完整分享文案，系统会提取视频网址" />
              </div>
              <div className="flex gap-3 justify-end">
                <button type="button" onClick={() => setShowAdd(false)} className="px-4 py-2 text-sm text-gray-400 hover:text-white">取消</button>
                <button type="submit" disabled={actionLoading === 'adding'}
                  className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 px-4 py-2 rounded-lg text-sm">
                  {actionLoading === 'adding' ? '添加中...' : '添加视频'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Content DNA */}
      {blogger.content_dna && (
        <div className="mb-8">
          <h3 className="text-lg font-semibold mb-4">内容基因</h3>
          <DnaVisualizer dna={blogger.content_dna} />
        </div>
      )}

      {/* Videos */}
      <div>
        <h3 className="text-lg font-semibold mb-4">视频素材 ({blogger.videos?.length || 0})</h3>
        {(!blogger.videos || blogger.videos.length === 0) ? (
          <p className="text-gray-500 text-sm">还没有添加视频，请点击"添加视频"开始</p>
        ) : (
          <div className="space-y-3">
            {blogger.videos.map((v) => (
              <div key={v.id} className="bg-gray-900 rounded-lg p-4 border border-gray-800">
                <div className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{v.title || v.source_url}</p>
                    <div className="flex items-center gap-2 mt-1">
                      <span className={`text-xs px-2 py-0.5 rounded ${statusColors[v.status] || statusColors.pending}`}>
                        {v.status}
                      </span>
                      {v.has_transcript && <span className="text-xs text-green-400">已转写</span>}
                    </div>
                    {v.error_message && <p role="alert" className="mt-2 break-words text-xs leading-relaxed text-red-300">{v.error_message}</p>}
                    {v.video_url && <details className="mt-2 text-xs text-slate-400"><summary className="cursor-pointer">预览导入的真实视频</summary><video controls preload="none" src={v.video_url} className="mt-2 max-h-96 max-w-full rounded-lg" /></details>}
                    {v.transcript_preview && (
                      <p className="text-xs text-gray-500 mt-2 line-clamp-2">{v.transcript_preview}</p>
                    )}
                  </div>
                  <div className="flex gap-2 ml-4">
                    {(['downloaded', 'pending', 'error'].includes(v.status)) && (
                      <button onClick={() => handleDownload(v.id)} disabled={actionLoading === `download-${v.id}`}
                        className="flex items-center gap-1 px-3 py-1.5 bg-green-600/20 text-green-400 rounded text-xs hover:bg-green-600/30 transition-colors">
                        <Mic size={14} />
                        {actionLoading === `download-${v.id}` ? '处理中...' : v.status === 'error' ? '重新导入' : '下载并转写'}
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
