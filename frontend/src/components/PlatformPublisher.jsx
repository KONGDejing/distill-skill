const OFFICIAL_PORTALS = {
  douyin: 'https://creator.douyin.com/',
  xiaohongshu: 'https://creator.xiaohongshu.com/',
  kuaishou: 'https://cp.kuaishou.com/',
  bilibili: 'https://member.bilibili.com/',
}

export default function PlatformPublisher({ platform, platformName, initialJob, packageStatus }) {
  const result = initialJob?.result
  const uncertain = result?.submission_uncertain === true || initialJob?.status === 'uncertain' || packageStatus === 'uncertain'
  const historicalReceipt = initialJob?.status === 'submitted' || packageStatus === 'submitted'
  const officialUrl = OFFICIAL_PORTALS[platform]

  return (
    <div className="mt-3 space-y-2 border-t border-white/[0.06] pt-3 text-[10px] leading-relaxed">
      <p className="font-medium text-amber-200">{platformName || platform}仅支持手动发布</p>
      <p className="text-slate-400">下载完整素材包后，请本人使用官方 App 或创作者中心上传。工厂不再登录账号、上传、提交或自动查看发布结果。</p>
      {uncertain ? <p className="text-amber-200">历史提交结果仍不确定，请先在官方页面自行核对，避免重复上传。</p>
        : historicalReceipt ? <p className="text-slate-500">历史提交回执已保留，不代表已公开发布。最新审核与公开状态请在官方页面自行核对。</p>
          : initialJob && <p className="text-slate-500">历史任务记录已保留，不会继续执行或自动重试。</p>}
      {officialUrl && <a href={officialUrl} target="_blank" rel="noopener noreferrer" className="inline-block text-sky-300 hover:text-sky-200">手动打开{platformName || platform}创作者中心</a>}
    </div>
  )
}
