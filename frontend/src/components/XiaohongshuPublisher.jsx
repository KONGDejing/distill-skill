export default function XiaohongshuPublisher({ initialJob, packageStatus }) {
  const historicalSubmission = initialJob?.status === 'submitted' || packageStatus === 'submitted'
  return (
    <div className="mt-3 space-y-2 border-t border-white/[0.06] pt-3 text-[10px] leading-relaxed">
      <p className="font-medium text-amber-200">小红书仅支持手动发布</p>
      <p className="text-slate-400">浏览器自动化已停用。请下载素材包后，使用小红书官方 App 或创作者中心手动上传、查看数据及发布结果。</p>
      <p className="text-slate-500">尚未接入官方授权自动化能力，不能通过登录或设置重新启用。</p>
      {historicalSubmission && <p className="text-slate-500">历史提交记录已保留；最新审核或公开状态请在官方页面自行核对。</p>}
      <a href="https://creator.xiaohongshu.com/" target="_blank" rel="noopener noreferrer" className="inline-block text-sky-300 hover:text-sky-200">手动打开小红书创作者中心</a>
    </div>
  )
}
