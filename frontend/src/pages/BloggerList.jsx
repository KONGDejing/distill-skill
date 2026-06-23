import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Plus, Trash2, RefreshCw } from 'lucide-react'
import { api } from '../api/client'

export default function BloggerList() {
  const [bloggers, setBloggers] = useState([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({ name: '', platform: 'other', profile_url: '' })
  const [error, setError] = useState('')

  useEffect(() => { loadBloggers() }, [])

  async function loadBloggers() {
    try {
      const res = await api.listBloggers()
      setBloggers(res.bloggers || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleAdd(e) {
    e.preventDefault()
    setError('')
    try {
      await api.createBlogger(form)
      setShowAdd(false)
      setForm({ name: '', platform: 'other', profile_url: '' })
      loadBloggers()
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleDelete(id) {
    if (!confirm('确定删除这个博主吗？所有相关数据将被清除。')) return
    try {
      await api.deleteBlogger(id)
      loadBloggers()
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleReAnalyze(id) {
    try {
      await api.analyzeBlogger(id)
      alert('已触发重新分析，请稍后刷新查看结果')
      loadBloggers()
    } catch (e) {
      setError(e.message)
    }
  }

  const platformLabels = { douyin: '抖音', xiaohongshu: '小红书', kuaishou: '快手', other: '其他' }
  const statusColors = {
    pending: 'bg-gray-500/20 text-gray-400',
    analyzing: 'bg-blue-500/20 text-blue-400',
    ready: 'bg-green-500/20 text-green-400',
    error: 'bg-red-500/20 text-red-400',
  }

  if (loading) return <div className="text-gray-500 animate-pulse">加载中...</div>

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">博主管理</h2>
        <button onClick={() => setShowAdd(true)} className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors">
          <Plus size={16} /> 添加博主
        </button>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-400 p-3 rounded-lg mb-4 text-sm">{error}</div>}

      {/* Add dialog */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-900 rounded-xl p-6 w-full max-w-md border border-gray-800">
            <h3 className="text-lg font-semibold mb-4">添加博主</h3>
            <form onSubmit={handleAdd} className="space-y-4">
              <div>
                <label className="block text-sm text-gray-400 mb-1">博主名称 *</label>
                <input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500" placeholder="例如：XX创业说" />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">平台</label>
                <select value={form.platform} onChange={e => setForm({ ...form, platform: e.target.value })}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500">
                  {Object.entries(platformLabels).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">博主主页链接</label>
                <input value={form.profile_url} onChange={e => setForm({ ...form, profile_url: e.target.value })}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500" placeholder="https://..." />
              </div>
              <div className="flex gap-3 justify-end pt-2">
                <button type="button" onClick={() => setShowAdd(false)} className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors">取消</button>
                <button type="submit" className="bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded-lg text-sm transition-colors">确认添加</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Blogger list */}
      <div className="grid gap-4">
        {bloggers.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <p className="text-lg">还没有添加博主</p>
            <p className="text-sm mt-1">点击"添加博主"开始蒸馏分析</p>
          </div>
        ) : (
          bloggers.map((b) => (
            <div key={b.id} className="bg-gray-900 rounded-xl p-5 border border-gray-800 hover:border-gray-700 transition-colors">
              <div className="flex items-center justify-between">
                <Link to={`/bloggers/${b.id}`} className="flex-1">
                  <div className="flex items-center gap-4">
                    <div className="w-12 h-12 rounded-full bg-gray-800 flex items-center justify-center text-lg font-bold text-blue-400">
                      {b.name[0]}
                    </div>
                    <div>
                      <h3 className="font-semibold hover:text-blue-400 transition-colors">{b.name}</h3>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-xs text-gray-500">{platformLabels[b.platform] || b.platform}</span>
                        <span className="text-xs text-gray-700">·</span>
                        <span className="text-xs text-gray-500">{b.video_count || 0} 个视频</span>
                        <span className="text-xs text-gray-700">·</span>
                        <span className={`text-xs px-2 py-0.5 rounded-full ${statusColors[b.status] || statusColors.pending}`}>
                          {b.status === 'ready' ? '已分析' : b.status === 'analyzing' ? '分析中' : b.status === 'error' ? '异常' : '待分析'}
                        </span>
                        {b.has_dna && <span className="text-xs text-green-400">· 内容基因已提取</span>}
                      </div>
                    </div>
                  </div>
                </Link>
                <div className="flex items-center gap-2">
                  <button onClick={() => handleReAnalyze(b.id)} className="p-2 text-gray-500 hover:text-blue-400 transition-colors" title="重新分析">
                    <RefreshCw size={16} />
                  </button>
                  <button onClick={() => handleDelete(b.id)} className="p-2 text-gray-500 hover:text-red-400 transition-colors" title="删除">
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
