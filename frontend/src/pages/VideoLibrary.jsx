import { useState, useEffect } from 'react'
import { Trash2, Download } from 'lucide-react'
import { api } from '../api/client'

export default function VideoLibrary() {
  const [videos, setVideos] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => { loadVideos() }, [])

  async function loadVideos() {
    try {
      const res = await api.listGeneratedVideos()
      setVideos(res.videos || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleDelete(id) {
    if (!confirm('确定删除这个视频吗？')) return
    try {
      await api.deleteGeneratedVideo(id)
      loadVideos()
    } catch (e) { setError(e.message) }
  }

  if (loading) return <div className="text-gray-500 animate-pulse">加载中...</div>

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">视频库</h2>

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-400 p-3 rounded-lg mb-4 text-sm">{error}</div>}

      {videos.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          <p className="text-lg">还没有生成视频</p>
          <p className="text-sm mt-1">在内容日历中审核通过文案后，点击"生成视频"</p>
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {videos.map((v) => (
            <div key={v.id} className="bg-gray-900 rounded-xl overflow-hidden border border-gray-800 hover:border-gray-700 transition-colors">
              {/* Video thumbnail / player */}
              <div className="aspect-[9/16] bg-gray-800 flex items-center justify-center relative group">
                {v.video_path ? (
                  <video src={api.downloadVideoUrl(v.id)} controls className="w-full h-full object-cover" />
                ) : (
                  <div className="text-gray-600 text-sm">视频生成中...</div>
                )}
              </div>

              {/* Info */}
              <div className="p-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs text-gray-400">ID: {v.id.slice(0, 8)}</p>
                    <p className="text-xs text-gray-500 mt-0.5">
                      {v.duration ? `${v.duration}秒` : '未知时长'} · {v.status === 'ready' ? '已完成' : v.status}
                    </p>
                    <p className="text-xs text-gray-600 mt-0.5">
                      {v.created_at ? new Date(v.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '未知时间'}
                    </p>
                  </div>
                  <div className="flex gap-1">
                    {v.status === 'ready' && (
                      <a href={api.downloadVideoUrl(v.id)} download
                        className="p-1.5 text-blue-400 hover:bg-blue-500/10 rounded transition-colors" title="下载">
                        <Download size={16} />
                      </a>
                    )}
                    <button onClick={() => handleDelete(v.id)}
                      className="p-1.5 text-gray-400 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors" title="删除">
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
