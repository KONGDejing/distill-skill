import { useState, useEffect } from 'react'
import { Upload, Volume2 } from 'lucide-react'
import { api } from '../api/client'

export default function Settings() {
  const [settings, setSettings] = useState(null)
  const [ttsVoices, setTTSVoices] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [success, setSuccess] = useState('')

  useEffect(() => { loadSettings() }, [])

  async function loadSettings() {
    try {
      const [settingsRes, voicesRes] = await Promise.all([
        api.getSettings(),
        api.listTTSSVoices(),
      ])
      setSettings(settingsRes)
      setTTSVoices(voicesRes.voices || [])
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
      const result = await api.uploadVoiceSample(file)
      await loadSettings()
      setSuccess(result.clone_engine_configured ? '声音样本上传成功，已启用克隆音色' : '声音样本上传成功，等待配置克隆引擎')
      setTimeout(() => setSuccess(''), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
      e.target.value = ''
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
              {settings?.photo_path ? (
                <img src={settings.photo_path.replace('/data/kongdejing/workspace/kdj/distill-skill/backend/', '/storage/')} alt="用户照片" className="w-full h-full object-cover" />
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

        {/* TTS Voice */}
        <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
          <h3 className="font-semibold mb-4">AI 语音音色</h3>
          <p className="text-sm text-gray-400 mb-3">选择视频配音的音色</p>
          <div className="grid grid-cols-2 gap-2">
            {ttsVoices.map((v) => (
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

        {/* Voice clone */}
        <div className="bg-gray-900 rounded-xl p-6 border border-gray-800">
          <h3 className="font-semibold mb-4">我的声音克隆</h3>
          <p className="text-sm text-gray-400 mb-3">上传你的声音样本，后续视频会优先使用克隆音色。建议上传 30 秒以上、环境安静、只有你本人说话的音频。</p>
          <div className="flex items-center justify-between gap-4 mb-4">
            <div className="text-sm">
              <p className={settings?.voice_clone_ready ? 'text-green-400' : 'text-gray-400'}>
                {settings?.voice_clone_ready ? '已上传声音样本' : '未上传声音样本'}
              </p>
              <p className="text-xs text-gray-500 mt-1">
                {settings?.voice_clone_enabled ? '当前已启用克隆音色' : '当前未启用克隆音色'}
              </p>
            </div>
            <label className="inline-flex items-center gap-2 bg-gray-800 hover:bg-gray-700 px-4 py-2 rounded-lg text-sm cursor-pointer transition-colors">
              <Upload size={16} />
              {saving ? '上传中...' : '上传声音样本'}
              <input type="file" accept="audio/*" onChange={handleVoiceUpload} className="hidden" disabled={saving} />
            </label>
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => handleUpdate({ voice_clone_enabled: true })}
              disabled={!settings?.voice_clone_ready || saving}
              className="bg-blue-600 hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed px-4 py-2 rounded-lg text-sm transition-colors">
              启用克隆音色
            </button>
            <button
              onClick={() => handleUpdate({ voice_clone_enabled: false })}
              disabled={saving}
              className="bg-gray-800 hover:bg-gray-700 px-4 py-2 rounded-lg text-sm transition-colors">
              使用系统音色
            </button>
          </div>
          <p className="text-xs text-gray-500 mt-3">如果未配置本地克隆引擎，系统会保存样本并自动回退到当前 Edge-TTS 音色。</p>
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
