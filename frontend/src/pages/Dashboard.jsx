import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Users, FileText, Video, TrendingUp } from 'lucide-react'
import { api } from '../api/client'

export default function Dashboard() {
  const [stats, setStats] = useState({ bloggers: 0, scripts: 0, videos: 0, pending: 0 })
  const [pendingScripts, setPendingScripts] = useState([])
  const [recentVideos, setRecentVideos] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    loadData()
  }, [])

  async function loadData() {
    try {
      const [bloggersRes, scriptsRes, videosRes] = await Promise.all([
        api.listBloggers(),
        api.listScripts({ status: 'pending' }),
        api.listGeneratedVideos(),
      ])
      const allScripts = await api.listScripts()

      setStats({
        bloggers: bloggersRes.bloggers?.length || 0,
        scripts: allScripts.scripts?.length || 0,
        videos: videosRes.videos?.length || 0,
        pending: scriptsRes.scripts?.length || 0,
      })
      setPendingScripts((scriptsRes.scripts || []).slice(0, 5))
      setRecentVideos((videosRes.videos || []).slice(0, 4))
    } catch (e) {
      console.error('Failed to load dashboard:', e)
    } finally {
      setLoading(false)
    }
  }

  const statCards = [
    { label: '博主数量', value: stats.bloggers, icon: Users, color: 'text-blue-400', bg: 'bg-blue-500/10' },
    { label: '累计文案', value: stats.scripts, icon: FileText, color: 'text-green-400', bg: 'bg-green-500/10' },
    { label: '产出视频', value: stats.videos, icon: Video, color: 'text-purple-400', bg: 'bg-purple-500/10' },
    { label: '待审核', value: stats.pending, icon: TrendingUp, color: 'text-orange-400', bg: 'bg-orange-500/10' },
  ]

  if (loading) {
    return <div className="text-gray-500 animate-pulse">加载中...</div>
  }

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">仪表盘</h2>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        {statCards.map(({ label, value, icon: Icon, color, bg }) => (
          <div key={label} className={`${bg} rounded-xl p-5 border border-gray-800`}>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-400">{label}</p>
                <p className={`text-3xl font-bold mt-1 ${color}`}>{value}</p>
              </div>
              <Icon className={color} size={32} />
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Pending scripts */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-lg font-semibold">待审核文案</h3>
            <Link to="/calendar" className="text-sm text-blue-400 hover:underline">查看全部</Link>
          </div>
          <div className="space-y-2">
            {pendingScripts.length === 0 ? (
              <p className="text-gray-500 text-sm py-4">暂无待审核文案</p>
            ) : (
              pendingScripts.map((s) => (
                <div key={s.id} className="bg-gray-900 rounded-lg p-4 border border-gray-800 hover:border-gray-700 transition-colors">
                  <p className="font-medium text-sm truncate">{s.title || '未命名'}</p>
                  <p className="text-xs text-gray-500 mt-1 truncate">{s.hook}</p>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Recent videos */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-lg font-semibold">最近生成的视频</h3>
            <Link to="/videos" className="text-sm text-blue-400 hover:underline">查看全部</Link>
          </div>
          <div className="space-y-2">
            {recentVideos.length === 0 ? (
              <p className="text-gray-500 text-sm py-4">暂无生成视频</p>
            ) : (
              recentVideos.map((v) => (
                <div key={v.id} className="bg-gray-900 rounded-lg p-4 border border-gray-800">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm text-gray-300">视频 {v.id.slice(0, 8)}</p>
                      <p className="text-xs text-gray-500">{v.duration ? `${v.duration}秒` : ''} · {v.status}</p>
                    </div>
                    <span className={`text-xs px-2 py-1 rounded ${v.status === 'ready' ? 'bg-green-500/20 text-green-400' : 'bg-yellow-500/20 text-yellow-400'}`}>
                      {v.status === 'ready' ? '可下载' : v.status}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
