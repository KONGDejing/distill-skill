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
  const [actionLoading, setActionLoading] = useState('')

  useEffect(() => { loadBlogger() }, [id])

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
      await api.addVideo(id, { source_url: videoUrl })
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
      await api.transcribeVideo(videoId)
      alert('已触发下载和转写任务，请稍后刷新页面查看结果')
      setTimeout(() => loadBlogger(), 3000)
    } catch (e) {
      alert('触发失败: ' + e.message)
    } finally {
      setActionLoading('')
    }
  }

  async function handleAnalyze() {
    setActionLoading('analyze')
    try {
      await api.analyzeBlogger(id)
      alert('已触发蒸馏分析，请稍后刷新页面查看结果')
      setTimeout(() => loadBlogger(), 5000)
    } catch (e) {
      setError(e.message)
    } finally {
      setActionLoading('')
    }
  }

  const statusColors = {
    pending: 'bg-gray-500/20 text-gray-400',
    downloading: 'bg-yellow-500/20 text-yellow-400',
    downloaded: 'bg-blue-500/20 text-blue-400',
    transcribing: 'bg-yellow-500/20 text-yellow-400',
    transcribed: 'bg-green-500/20 text-green-400',
    error: 'bg-red-500/20 text-red-400',
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
          <button onClick={handleAnalyze} disabled={!hasTranscripts || actionLoading === 'analyze'}
            className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed px-4 py-2 rounded-lg text-sm transition-colors">
            <RefreshCw size={16} className={actionLoading === 'analyze' ? 'animate-spin' : ''} />
            {blogger.content_dna ? '重新蒸馏' : '开始蒸馏分析'}
          </button>
        </div>
      </div>

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
                  placeholder="粘贴抖音/小红书/快手视频链接" />
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
                    {v.transcript_preview && (
                      <p className="text-xs text-gray-500 mt-2 line-clamp-2">{v.transcript_preview}</p>
                    )}
                  </div>
                  <div className="flex gap-2 ml-4">
                    {(v.status === 'downloaded' || v.status === 'pending') && (
                      <button onClick={() => handleDownload(v.id)} disabled={actionLoading === `download-${v.id}`}
                        className="flex items-center gap-1 px-3 py-1.5 bg-green-600/20 text-green-400 rounded text-xs hover:bg-green-600/30 transition-colors">
                        <Mic size={14} />
                        {actionLoading === `download-${v.id}` ? '处理中...' : '下载并转写'}
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
