import { useState, useEffect, useRef } from 'react'
import { Upload, Volume2, Trash2, Edit3, Check, X, Mic } from 'lucide-react'
import { api } from '../api/client'

export default function Settings() {
  const [settings, setSettings] = useState(null)
  const [ttsVoices, setTTSVoices] = useState([])
  const [voiceSamples, setVoiceSamples] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [success, setSuccess] = useState('')
  const [voiceName, setVoiceName] = useState('')
  const [editingId, setEditingId] = useState(null)
  const [editName, setEditName] = useState('')
  const voiceInputRef = useRef(null)

  useEffect(() => { loadSettings() }, [])

  async function loadSettings() {
    try {
      const [settingsRes, voicesRes] = await Promise.all([
        api.getSettings(),
        api.listTTSSVoices(),
      ])
      setSettings(settingsRes)
      setTTSVoices(voicesRes.voices || [])
      setVoiceSamples(settingsRes.voice_clone_samples || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handlePhotoUpload(e) {
    const file = e.target.files[0]
    if (!file) return
    setSaving(true)
    setError('')
    try {
      await api.uploadPhoto(file)
      await loadSettings()
      setSuccess('照片上传成功')
      setTimeout(() => setSuccess(''), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleVoiceUpload(e) {
    const file = e.target.files[0]
    if (!file) return
    setSaving(true)
    setError('')
    try {
      const result = await api.uploadVoiceSample(file, voiceName)
      setVoiceSamples(result.voice_clone_samples || [])
      await loadSettings()
      setVoiceName('')
      if (voiceInputRef.current) voiceInputRef.current.value = ''
      setSuccess('声音样本已保存，并已加入 AI 语音音色')
      setTimeout(() => setSuccess(''), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
      e.target.value = ''
    }
  }

  async function handleRename(sampleId) {
    if (!editName.trim()) return
    try {
      const result = await api.renameVoiceSample(sampleId, editName.trim())
      setVoiceSamples(result.voice_clone_samples || [])
      await loadSettings()
      setEditingId(null)
      setSuccess('重命名成功')
      setTimeout(() => setSuccess(''), 3000)
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleDelete(sampleId) {
    if (!confirm('确定删除这个声音样本吗？')) return
    try {
      const result = await api.deleteVoiceSample(sampleId)
      setVoiceSamples(result.voice_clone_samples || [])
      await loadSettings()
      setSuccess('已删除')
      setTimeout(() => setSuccess(''), 3000)
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleUpdate(data) {
    setSaving(true)
    setError('')
    try {
      await api.updateSettings(data)
      await loadSettings()
      setSuccess('设置已保存')
      setTimeout(() => setSuccess(''), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="text-gray-500 animate-pulse">加载中...</div>

  const cloneVoices = ttsVoices.filter(v => v.is_clone)
  const systemVoices = ttsVoices.filter(v => !v.is_clone)

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">系统设置</h2>

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-400 p-3 rounded-lg mb-4 text-sm">{error}</div>}
      {success && <div className="bg-green-500/10 border border-green-500/30 text-green-400 p-3 rounded-lg mb-4 text-sm">{success}</div>}

      <div className="space-y-6 max-w-2xl">
        {/* Photo upload */}
        <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
          <h3 className="font-semibold mb-4">我的照片</h3>
          <div className="flex items-center gap-6">
            <div className="w-24 h-24 rounded-full bg-gray-800 overflow-hidden flex items-center justify-center border-2 border-gray-700">
              {settings?.photo_url ? (
                <img src={settings.photo_url} alt="用户照片" className="w-full h-full object-cover" />
              ) : (
                <span className="text-gray-600 text-xs">无照片</span>
              )}
            </div>
            <div>
              <p className="text-sm text-gray-400 mb-2">上传你的照片，将用作视频背景</p>
              <label className="inline-flex items-center gap-2 bg-gray-800 hover:bg-gray-700 px-4 py-2 rounded-lg text-sm cursor-pointer transition-colors">
                <Upload size={16} />
                {saving ? '上传中...' : '选择照片'}
                <input type="file" accept="image/*" onChange={handlePhotoUpload} className="hidden" disabled={saving} />
              </label>
            </div>
          </div>
        </div>

        {/* Voice samples management */}
        <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
          <h3 className="font-semibold mb-4">我的声音样本</h3>
          <p className="text-sm text-gray-400 mb-4">上传你的声音样本（30秒以上，环境安静，仅本人说话），命名后可重复使用。</p>

          {/* Upload with name */}
          <div className="flex items-center gap-3 mb-4">
            <input
              value={voiceName}
              onChange={(e) => setVoiceName(e.target.value)}
              placeholder="给声音起个名字，如：我的声音"
              className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            />
            <label className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded-lg text-sm cursor-pointer transition-colors flex-shrink-0">
              <Upload size={16} />
              {saving ? '上传中...' : '上传'}
              <input ref={voiceInputRef} type="file" accept="audio/*" onChange={handleVoiceUpload} className="hidden" disabled={saving} />
            </label>
          </div>

          {/* Sample list */}
          {voiceSamples.length === 0 ? (
            <p className="text-xs text-gray-600">还没有上传声音样本</p>
          ) : (
            <div className="space-y-2">
              {voiceSamples.map((sample) => (
                <div key={sample.id} className="flex items-center justify-between bg-gray-800 rounded-lg px-3 py-2.5">
                  {editingId === sample.id ? (
                    <div className="flex items-center gap-2 flex-1">
                      <input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="flex-1 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm focus:outline-none focus:border-blue-500"
                        autoFocus
                      />
                      <button onClick={() => handleRename(sample.id)} className="p-1 text-green-400 hover:bg-green-500/10 rounded">
                        <Check size={14} />
                      </button>
                      <button onClick={() => setEditingId(null)} className="p-1 text-gray-400 hover:text-white rounded">
                        <X size={14} />
                      </button>
                    </div>
                  ) : (
                    <>
                      <div className="flex items-center gap-2">
                        <Mic size={14} className="text-purple-400" />
                        <span className="text-sm">{sample.name}</span>
                      </div>
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => { setEditingId(sample.id); setEditName(sample.name || '') }}
                          className="p-1 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors" title="重命名">
                          <Edit3 size={14} />
                        </button>
                        <button
                          onClick={() => handleDelete(sample.id)}
                          className="p-1 text-gray-400 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors" title="删除">
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* TTS Voice selection */}
        <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
          <h3 className="font-semibold mb-4">AI 语音音色</h3>
          <p className="text-sm text-gray-400 mb-3">选择视频配音的音色。上传克隆声音后，这里会出现你的声音选项。</p>

          {/* Clone voices */}
          {cloneVoices.length > 0 && (
            <>
              <p className="text-xs text-purple-400 font-medium mb-2">我的克隆音色</p>
              <div className="grid grid-cols-2 gap-2 mb-4">
                {cloneVoices.map((v) => (
                  <button key={v.id} onClick={() => handleUpdate({ tts_voice: v.id })}
                    className={`flex items-center gap-2 p-3 rounded-lg text-sm text-left transition-colors ${
                      settings?.tts_voice === v.id
                        ? 'bg-purple-600/20 border border-purple-500/30 text-purple-400'
                        : 'bg-gray-800 hover:bg-gray-750 text-gray-300 border border-transparent'
                    }`}>
                    <Mic size={16} />
                    <div>
                      <p className="font-medium">{v.name}</p>
                      <p className="text-xs opacity-60">克隆音色</p>
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}

          <p className="text-xs text-blue-400 font-medium mb-2">系统音色</p>
          <div className="grid grid-cols-2 gap-2">
            {systemVoices.map((v) => (
              <button key={v.id} onClick={() => handleUpdate({ tts_voice: v.id })}
                className={`flex items-center gap-2 p-3 rounded-lg text-sm text-left transition-colors ${
                  settings?.tts_voice === v.id
                    ? 'bg-blue-600/20 border border-blue-500/30 text-blue-400'
                    : 'bg-gray-800 hover:bg-gray-750 text-gray-300 border border-transparent'
                }`}>
                <Volume2 size={16} />
                <div>
                  <p className="font-medium">{v.name}</p>
                  <p className="text-xs opacity-60">{v.gender === 'male' ? '男声' : '女声'}</p>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Watermark */}
        <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
          <h3 className="font-semibold mb-4">账号水印</h3>
          <p className="text-sm text-gray-400 mb-3">显示在视频角落的账号名称</p>
          <div className="flex gap-3">
            <input
              value={settings?.watermark || ''}
              onChange={(e) => setSettings({ ...settings, watermark: e.target.value })}
              className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
              placeholder="如：@你的账号名"
            />
            <button onClick={() => handleUpdate({ watermark: settings?.watermark || '' })}
              className="bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded-lg text-sm transition-colors">
              保存
            </button>
          </div>
        </div>

        {/* Video style */}
        <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
          <h3 className="font-semibold mb-4">视频输出配置</h3>
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <label className="text-gray-400 text-xs">分辨率</label>
              <p className="mt-1">1080 × 1920 (竖屏)</p>
            </div>
            <div>
              <label className="text-gray-400 text-xs">帧率</label>
              <p className="mt-1">30 FPS</p>
            </div>
            <div>
              <label className="text-gray-400 text-xs">编码</label>
              <p className="mt-1">H.264 + AAC</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
