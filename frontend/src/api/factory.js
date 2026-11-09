async function request(path = '', options = {}) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 20000)
  try {
    const response = await fetch(`/api/factory${path}`, {
      ...options,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...options.headers },
    })
    const data = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = data?.detail
      throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(v => v.msg).join('；') : `请求失败（${response.status}）`)
    }
    if (data == null) throw new Error('工厂服务返回异常，请稍后重试。')
    return data
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('连接工厂服务超时，正在等待下一次连接。')
    if (error instanceof TypeError) throw new Error('暂时无法连接工厂服务，请检查网络或服务状态。')
    throw error
  } finally {
    clearTimeout(timeout)
  }
}

export const factoryApi = {
  overview: () => request(),
  getRun: (id) => request(`/runs/${encodeURIComponent(id)}`),
  createRun: (targetCount) => request('/runs', { method: 'POST', body: JSON.stringify({ target_count: targetCount }) }),
  cancelRun: (id) => request(`/runs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  retryRun: (id) => request(`/runs/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
  updateSettings: (data) => request('/settings', { method: 'PATCH', body: JSON.stringify(data) }),
  prepareXiaohongshu: (itemId) => request(`/items/${encodeURIComponent(itemId)}/platforms/xiaohongshu/prepare`, { method: 'POST' }),
  getPlatformJob: (jobId) => request(`/platform-jobs/${encodeURIComponent(jobId)}`),
  publishPlatformJob: (jobId) => request(`/platform-jobs/${encodeURIComponent(jobId)}/publish`, { method: 'POST' }),
}
