import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import test from 'node:test'
import { build } from 'esbuild'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const entry = fileURLToPath(new URL('../src/pages/FactoryDashboard.jsx', import.meta.url))
const result = await build({ entryPoints: [entry], bundle: true, write: false,
  platform: 'node', format: 'cjs', jsx: 'automatic', external: ['react', 'react/jsx-runtime'] })
const require = createRequire(import.meta.url)
const effects = []
const hookReact = { ...React, useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}],
  useRef: initial => ({ current: initial }), useEffect: effect => effects.push(effect) }
const forbidden = () => { throw new Error('Manual delivery must not call account APIs or start polling') }
const module = { exports: {} }
vm.runInNewContext(result.outputFiles[0].text, {
  module, exports: module.exports, require: name => name === 'react' ? hookReact : require(name),
  fetch: forbidden, setInterval: forbidden, setTimeout: forbidden,
})
const { SettingsPanel, ManualPackage, ManualDeliveryPlan, ManualPlatformChannels, ContentCard } = module.exports
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props))
const handoff = {
  title: '定稿标题', description: '最终发布正文\n#科技 #论文\nAI制作说明样例', caption: '旧版正文', tags: ['科技', '论文'], cover_text: '封面文字样例',
  hooks: ['开场一', '开场二', '开场三'], follow_prompt: '关注句样例', ai_disclosure: 'AI制作说明样例',
  pinned_comment: '本人置顶评论样例', reply_templates: [{ question: '真实问题样例', reply: '本人回复样例' }],
  publication_warning: '历史提交结果不确定，请本人先核对', distribution: { platform: 'douyin', day: '2026-11-11' },
}

test('saving a plan preserves Douyin publishing and keeps Xiaohongshu manual', () => {
  let saved
  const form = SettingsPanel({ settings: { daily_target: 6, manual_delivery_enabled: true,
    publish_enabled: true, auto_publish_xiaohongshu: true }, onSave: value => { saved = value } })
  form.props.onSubmit({ preventDefault() {} })
  assert.equal(saved.manual_delivery_enabled, true)
  assert.equal(saved.publish_enabled, true)
  assert.equal(saved.auto_publish_xiaohongshu, false)
  assert.match(renderToStaticMarkup(form), /启用人工交付计划/)
})

for (const [platform, name] of Object.entries({ douyin: '抖音', kuaishou: '快手', xiaohongshu: '小红书', bilibili: '哔哩哔哩' })) {
  test(`${platform} shows final title and description with optional archives`, () => {
    const html = render(ManualPackage, { pkg: { platform, status: 'package_ready', manual_handoff: {
      ...handoff, distribution: { platform, day: '2026-11-11' } },
      download_url: `/api/factory/items/fixture/packages/${platform}`, copy_url: `/storage/${platform}/copy.txt` } })
    for (const value of ['定稿标题', '最终发布正文', '#科技 #论文', 'AI制作说明样例',
      '本人置顶评论样例', '真实问题样例', '本人回复样例', '历史提交结果不确定，请本人先核对']) {
      assert.ok(html.includes(value), `Missing handoff value: ${value}`)
    }
    assert.ok(html.includes(`下载${name}归档 ZIP`))
    assert.match(html, /<details[^>]*><summary[^>]*>可选归档/)
    assert.match(html, /<summary[^>]*>可选评论回复/)
    assert.equal((html.match(/<dt[ >]/g) || []).length, 2)
    assert.doesNotMatch(html, /旧版正文|封面文字样例|开场一|开场二|开场三|关注句样例|开场钩子备选|自然关注句|使用前核对与成片开头/)
    assert.match(html, /这些问题是模板，不代表已收到留言/)
    assert.doesNotMatch(html, /<button|扫码登录/)
  })
}

test('historical packages cannot look like the current upload route', () => {
  const html = render(ManualPackage, { pkg: { platform: 'kuaishou', status: 'submitted',
    manual_handoff: handoff, download_url: '/api/factory/items/fixture/packages/kuaishou' },
    item: { distribution: { platform: 'douyin' } } })
  assert.match(html, /当前唯一分配平台为抖音，请勿上传此历史包/)
  assert.match(html, /下载快手历史素材包/)
  assert.doesNotMatch(html, /下载快手完整素材包|手动打开快手创作者中心/)
})

test('schedule is visible for either Douyin publishing or manual delivery', () => {
  const props = { target: 6, releases: [{ id: 'fixture', status: 'manual_ready', platforms: ['douyin'],
    due_at: '2026-11-11T00:00:00+08:00', nominal_at: '2026-11-11T00:00:00+08:00' }] }
  assert.equal(render(ManualDeliveryPlan, { ...props, settings: { manual_delivery_enabled: false, publish_enabled: false } }), '')
  assert.match(render(ManualDeliveryPlan, { ...props, settings: { manual_delivery_enabled: false, publish_enabled: true } }), /抖音自动发布/)
  const html = render(ManualDeliveryPlan, { ...props, settings: { manual_delivery_enabled: true, publish_enabled: false } })
  assert.match(html, /发布与交付计划/)
  assert.match(html, /建议发布时间/)
  assert.match(html, /快手、小红书到点只交付素材包/)
  assert.match(html, /每平台最多2条/)
})

test('connection effects cannot poll even with legacy enabled account data', () => {
  effects.length = 0
  const html = render(ManualPlatformChannels, { platforms: ['douyin', 'kuaishou', 'xiaohongshu', 'bilibili'].map(id => ({
    id, status: 'connected', automation: { enabled: id !== 'douyin' },
  })) })
  assert.doesNotMatch(html, /扫码登录|刷新登录状态|\/login/)
  assert.match(html, /快手和小红书由本人操作/)
  assert.equal(effects.length, 4)
  for (const effect of effects) {
    const cleanup = effect()
    if (cleanup) cleanup()
  }
})


test('main video downloads use the named API while playback keeps the original source', () => {
  const html = render(ContentCard, { index: 0, item: { id: 'fixture', title: '论文真正解决了什么',
    video_url: '/storage/fixture/video.mp4', video_download_url: '/api/factory/items/fixture/video',
    video_filename: '中文大标题.mp4' } })
  assert.match(html, /href="\/api\/factory\/items\/fixture\/video" download="中文大标题.mp4"/)
  assert.match(html, /<source src="\/storage\/fixture\/video.mp4" type="video\/mp4"/)
  assert.match(html, /下载完整成片/)
  assert.doesNotMatch(html, /下载已排字封面/)
})

test('legacy video downloads and previews use the Chinese title as filename', () => {
  const item = { title: '中文成片大标题', video_url: '/storage/fixture/video.mp4',
    preview: { title: '中文样片大标题', video_url: '/storage/fixture/preview.mp4' } }
  const html = render(ContentCard, { index: 0, item })
  assert.match(html, /href="\/storage\/fixture\/video.mp4" download="中文成片大标题.mp4"/)
  assert.match(html, /href="\/storage\/fixture\/preview.mp4" download="中文样片大标题.mp4"/)
  const fallback = render(ContentCard, { index: 0, item: { ...item, preview: { video_url: item.preview.video_url } } })
  assert.match(fallback, /href="\/storage\/fixture\/preview.mp4" download="中文成片大标题.mp4"/)
})

test('uncertain historical submissions remain prominent without extra editing steps', () => {
  const html = render(ManualPackage, { pkg: { platform: 'douyin', status: 'submitted',
    manual_handoff: { title: '定稿', description: '最终正文' } },
    item: { platform_jobs: { douyin: { status: 'failed', result: { submission_uncertain: true } } } } })
  assert.match(html, /历史提交结果仍不确定/)
  assert.match(html, /避免重复上传/)
  assert.doesNotMatch(html, /历史提交回执已保留/)
})

test('legacy copy uses existing text and the cover is an optional archive', () => {
  const html = render(ManualPackage, { pkg: { platform: 'kuaishou',
    manual_handoff: { title: '旧标题', caption: '已有正文', tags: ['科技'], ai_disclosure: '已有AI说明' } } })
  assert.match(html, /已有正文/)
  assert.match(html, /#科技/)
  assert.match(html, /已有AI说明/)
  const card = render(ContentCard, { index: 0, item: { title: '完整成片标题', video_url: '/storage/video.mp4', cover_url: '/storage/cover.png' } })
  assert.match(card, /<details[^>]*><summary[^>]*>可选封面归档<\/summary><a[^>]*href="\/storage\/cover.png"/)
  assert.match(card, /下载完整成片/)
})


test('only authorized Douyin renders login controls, even if another platform sends a legacy enabled flag', () => {
  const html = render(ManualPlatformChannels, { platforms: ['douyin', 'kuaishou', 'xiaohongshu', 'bilibili'].map(id => ({ id, automation: { enabled: true } })) })
  assert.match(html, /抖音自动|自动发布/)
  assert.match(html, /\/platforms\/douyin\/login/)
  for (const id of ['kuaishou', 'xiaohongshu', 'bilibili']) assert.ok(!html.includes(`/platforms/${id}/login`))
  assert.match(html, /必须选择官方AI生成声明/)
})


test('Douyin package explains automatic delivery instead of asking the owner to upload again', () => {
  const html = render(ManualPackage, { pkg: { platform: 'douyin', status: 'prepared', automation: { enabled: true }, manual_handoff: handoff } })
  assert.match(html, /抖音按排期自动上传/)
  assert.match(html, /内容由AI生成/)
  assert.doesNotMatch(html, /上传完整成片（MP4），填写/)
  assert.match(html, /避免重复上传/)
})
