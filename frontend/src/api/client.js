const BASE = '/api';

async function request(url, options = {}) {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Bloggers
  listBloggers: () => request('/bloggers'),
  createBlogger: (data) => request('/bloggers', { method: 'POST', body: JSON.stringify(data) }),
  getBlogger: (id) => request(`/bloggers/${id}`),
  deleteBlogger: (id) => request(`/bloggers/${id}`, { method: 'DELETE' }),
  addVideo: (bloggerId, data) => request(`/bloggers/${bloggerId}/videos`, { method: 'POST', body: JSON.stringify(data) }),
  listBloggerVideos: (bloggerId) => request(`/bloggers/${bloggerId}/videos`),
  analyzeBlogger: (bloggerId) => request(`/bloggers/${bloggerId}/analyze`, { method: 'POST' }),

  // Videos
  getVideo: (id) => request(`/videos/${id}`),
  transcribeVideo: (id) => request(`/videos/${id}/transcribe`, { method: 'POST' }),
  deleteVideo: (id) => request(`/videos/${id}`, { method: 'DELETE' }),

  // Scripts
  listScripts: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/scripts${qs ? '?' + qs : ''}`);
  },
  getScript: (id) => request(`/scripts/${id}`),
  updateScript: (id, data) => request(`/scripts/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteScript: (id) => request(`/scripts/${id}`, { method: 'DELETE' }),
  restoreScript: (id) => request(`/scripts/${id}/restore`, { method: 'POST' }),
  permanentlyDeleteScript: (id) => request(`/scripts/${id}/permanent`, { method: 'DELETE' }),
  emptyTrash: () => request('/scripts/trash/empty', { method: 'DELETE' }),
  approveScript: (id) => request(`/scripts/${id}/approve`, { method: 'POST' }),
  rejectScript: (id) => request(`/scripts/${id}/reject`, { method: 'POST' }),
  generateScripts: (bloggerId) => request(`/scripts/generate?blogger_id=${bloggerId}`, { method: 'POST' }),
  generateVideo: (scriptId) => request(`/scripts/${scriptId}/generate-video`, { method: 'POST' }),

  // Generated Videos
  listGeneratedVideos: () => request('/videos-generated'),
  getGeneratedVideo: (id) => request(`/videos-generated/${id}`),
  deleteGeneratedVideo: (id) => request(`/videos-generated/${id}`, { method: 'DELETE' }),
  downloadVideoUrl: (id) => `${BASE}/videos-generated/${id}/download`,

  // Settings
  getSettings: () => request('/settings'),
  updateSettings: (data) => request('/settings', { method: 'PUT', body: JSON.stringify(data) }),
  uploadPhoto: async (file) => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${BASE}/settings/upload-photo`, { method: 'POST', body: form });
    return res.json();
  },
  uploadVoiceSample: async (file) => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${BASE}/settings/upload-voice-sample`, { method: 'POST', body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Request failed' }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },
  listTTSSVoices: () => request('/settings/tts-voices'),
};
