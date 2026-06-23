import { useState, useEffect } from 'react'
import { Play, Edit3, Check, X, Video, Trash2, RotateCcw } from 'lucide-react'
import { api } from '../api/client'

export default function ContentCalendar() {
  const [scripts, setScripts] = useState([])
  const [bloggers, setBloggers] = useState([])
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState('')
  const [editing, setEditing] = useState(null)
  const [editForm, setEditForm] = useState({})
  const [error, setError] = useState('')
  const [showTrash, setShowTrash] = useState(false)

  useEffect(() => { loadData() }, [showTrash])

  async function loadData() {
    try {
      const [scriptsRes, bloggersRes] = await Promise.all([
        api.listScripts(showTrash ? { status: 'trashed' } : {}),
        api.listBloggers(),
      ])
      setScripts(scriptsRes.scripts || [])
      setBloggers(bloggersRes.bloggers || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleGenerate(bloggerId) {
    setGenerating(bloggerId)
    setError('')
    try {
      await api.generateScripts(bloggerId)
      alert('已触发文案生成，请稍后刷新页面查看')
      setTimeout(() => loadData(), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setGenerating('')
    }
  }

  async function handleApprove(id) {
    try {
      await api.approveScript(id)
      loadData()
    } catch (e) { setError(e.message) }
  }

  async function handleReject(id) {
    try {
      await api.rejectScript(id)
      loadData()
    } catch (e) { setError(e.message) }
  }

  async function handleGenerateVideo(scriptId) {
    try {
      await api.generateVideo(scriptId)
      alert('已触发视频生成，请稍后查看视频库')
      loadData()
    } catch (e) { setError(e.message) }
  }

  async function handleDelete(id) {
    try {
      if (showTrash) {
        if (!confirm('确定要彻底删除这条文案吗？删除后不可恢复。')) return
        await api.permanentlyDeleteScript(id)
      } else {
        await api.deleteScript(id)
      }
      loadData()
    } catch (e) { setError(e.message) }
  }

  async function handleRestore(id) {
    try {
      await api.restoreScript(id)
      loadData()
    } catch (e) { setError(e.message) }
  }

  async function handleEmptyTrash() {
    try {
      if (!confirm('确定要彻底删除垃圾站中的所有文案吗？删除后不可恢复。')) return
      await api.emptyTrash()
      loadData()
    } catch (e) { setError(e.message) }
  }

  function openEdit(script) {
    setEditing(script.id)
    setEditForm({ title: script.title, script: script.script, hook: script.hook })
  }

  async function handleSaveEdit() {
    try {
      await api.updateScript(editing, editForm)
      setEditing(null)
      loadData()
    } catch (e) { setError(e.message) }
  }

  const statusColors = {
    pending: 'bg-yellow-500/20 text-yellow-400',
    approved: 'bg-green-500/20 text-green-400',
    rejected: 'bg-red-500/20 text-red-400',
    generating: 'bg-blue-500/20 text-blue-400',
    generated_video: 'bg-purple-500/20 text-purple-400',
    trashed: 'bg-gray-700 text-gray-300',
  }

  if (loading) return <div className="text-gray-500 animate-pulse">加载中...</div>

  const readyBloggers = bloggers.filter(b => b.has_dna || b.status === 'ready')

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">内容日历</h2>
        <div className="flex gap-2">
          <button onClick={() => setShowTrash(!showTrash)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm transition-colors ${showTrash ? 'bg-gray-700 text-white' : 'bg-gray-800 hover:bg-gray-700 text-gray-300'}`}>
            {showTrash ? <RotateCcw size={16} /> : <Trash2 size={16} />}
            {showTrash ? '返回内容日历' : '垃圾站'}
          </button>
          {showTrash && scripts.length > 0 && (
            <button onClick={handleEmptyTrash}
              className="flex items-center gap-2 bg-red-600 hover:bg-red-700 px-4 py-2 rounded-lg text-sm transition-colors">
              <Trash2 size={16} />
              清空垃圾站
            </button>
          )}
          {!showTrash && readyBloggers.map(b => (
            <button key={b.id} onClick={() => handleGenerate(b.id)} disabled={generating === b.id}
              className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 px-4 py-2 rounded-lg text-sm transition-colors">
              <Play size={16} className={generating === b.id ? 'animate-spin' : ''} />
              基于「{b.name}」生成文案
            </button>
          ))}
          {!showTrash && readyBloggers.length === 0 && (
            <span className="text-sm text-gray-500">请先完成博主蒸馏分析</span>
          )}
        </div>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-400 p-3 rounded-lg mb-4 text-sm">{error}</div>}

      <div className="space-y-3">
        {scripts.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <p className="text-lg">{showTrash ? '垃圾站为空' : '还没有生成文案'}</p>
            <p className="text-sm mt-1">{showTrash ? '删除到垃圾站的内容会显示在这里' : '选择一个已完成蒸馏的博主来生成今日文案'}</p>
          </div>
        ) : (
          scripts.map((s) => (
            <div key={s.id} className="bg-gray-900 rounded-xl p-5 border border-gray-800">
              {editing === s.id ? (
                /* Edit mode */
                <div className="space-y-3">
                  <input value={editForm.title} onChange={e => setEditForm({ ...editForm, title: e.target.value })}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500" />
                  <textarea value={editForm.hook} onChange={e => setEditForm({ ...editForm, hook: e.target.value })}
                    rows={1} className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500" placeholder="钩子" />
                  <textarea value={editForm.script} onChange={e => setEditForm({ ...editForm, script: e.target.value })}
                    rows={5} className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500" />
                  <div className="flex gap-2 justify-end">
                    <button onClick={() => setEditing(null)} className="px-3 py-1.5 text-sm text-gray-400 hover:text-white">取消</button>
                    <button onClick={handleSaveEdit} className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 rounded-lg">保存</button>
                  </div>
                </div>
              ) : (
                /* View mode */
                <div>
                  <div className="flex items-start justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <h3 className="font-semibold">{s.title || '未命名选题'}</h3>
                        <span className={`text-xs px-2 py-0.5 rounded ${statusColors[s.status] || ''}`}>
                          {s.status === 'pending' ? '待审核' : s.status === 'approved' ? '已通过' : s.status === 'rejected' ? '已驳回' : s.status === 'generated_video' ? '已生成视频' : s.status === 'trashed' ? '垃圾站' : s.status}
                        </span>
                      </div>
                      {s.hook && <p className="text-sm text-orange-400 mb-2">钩子: {s.hook}</p>}
                      <p className="text-sm text-gray-300 whitespace-pre-wrap line-clamp-3">{s.script}</p>
                      {s.hashtags && (
                        <div className="flex flex-wrap gap-1 mt-2">
                          {s.hashtags.map((t, i) => (
                            <span key={i} className="text-xs text-blue-400 bg-blue-500/10 px-2 py-0.5 rounded">#{t}</span>
                          ))}
                        </div>
                      )}
                      {s.visual_suggestion && (
                        <p className="text-xs text-gray-500 mt-2">画面建议: {s.visual_suggestion}</p>
                      )}
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-1 ml-4">
                      {showTrash ? (
                        <>
                          <button onClick={() => handleRestore(s.id)}
                            className="p-1.5 text-green-400 hover:bg-green-500/10 rounded transition-colors" title="恢复到内容日历">
                            <RotateCcw size={16} />
                          </button>
                          <button onClick={() => handleDelete(s.id)}
                            className="p-1.5 text-red-400 hover:bg-red-500/10 rounded transition-colors" title="彻底删除">
                            <Trash2 size={16} />
                          </button>
                        </>
                      ) : (
                        <>
                      {s.status === 'pending' && (
                        <>
                          <button onClick={() => handleApprove(s.id)}
                            className="p-1.5 text-green-400 hover:bg-green-500/10 rounded transition-colors" title="通过">
                            <Check size={16} />
                          </button>
                          <button onClick={() => handleReject(s.id)}
                            className="p-1.5 text-red-400 hover:bg-red-500/10 rounded transition-colors" title="驳回">
                            <X size={16} />
                          </button>
                        </>
                      )}
                      <button onClick={() => openEdit(s)}
                        className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors" title="编辑">
                        <Edit3 size={16} />
                      </button>
                      {['approved', 'pending'].includes(s.status) && (
                        <button onClick={() => handleGenerateVideo(s.id)}
                          className="p-1.5 text-purple-400 hover:bg-purple-500/10 rounded transition-colors" title="生成视频">
                          <Video size={16} />
                        </button>
                      )}
                      <button onClick={() => handleDelete(s.id)}
                        className="p-1.5 text-red-400 hover:bg-red-500/10 rounded transition-colors" title="删除到垃圾站">
                        <Trash2 size={16} />
                      </button>
                        </>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-3 mt-3 text-xs text-gray-600">
                    <span>{s.scheduled_date || (s.created_at ? new Date(s.created_at).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '')}</span>
                    <span>博主: {bloggers.find(b => b.id === s.blogger_id)?.name || s.blogger_id?.slice(0, 8)}</span>
                  </div>
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
