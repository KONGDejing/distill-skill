import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import test from 'node:test'
import { build } from 'esbuild'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const componentPath = fileURLToPath(new URL('../src/components/PlatformPublisher.jsx', import.meta.url))
const compiled = await build({ entryPoints: [componentPath], bundle: true, write: false, metafile: true,
  platform: 'node', format: 'cjs', jsx: 'automatic', external: ['react', 'react/jsx-runtime'] })
const forbidden = () => { throw new Error('Manual publisher must not access APIs, timers, or local storage') }
const module = { exports: {} }
vm.runInNewContext(compiled.outputFiles[0].text, {
  module, exports: module.exports, require: createRequire(import.meta.url),
  fetch: forbidden, setInterval: forbidden, setTimeout: forbidden,
  localStorage: new Proxy({}, { get: forbidden }),
})
const Publisher = module.exports.default

for (const platform of ['douyin', 'xiaohongshu', 'kuaishou', 'bilibili']) {
  test(`${platform} is manual even with pending jobs and cached authenticated capability`, () => {
    const html = renderToStaticMarkup(React.createElement(Publisher, {
      platform, platformName: '测试平台', initialJob: { id: 'historic', status: 'running' },
      publicationJobId: 'historic', packageStatus: 'prepared',
      connectionState: { authenticated: true, session_active: true, capabilities: { prepare: true, publish: true } },
    }))
    assert.match(html, /仅支持手动发布/)
    assert.match(html, /不会继续执行或自动重试/)
    assert.doesNotMatch(html, /<button|\/api\/|扫码登录/)
    // No imported API module or legacy publisher effect can schedule polling on mount.
    assert.equal(Object.keys(compiled.metafile.inputs).length, 1)
    assert.deepEqual(compiled.metafile.outputs[Object.keys(compiled.metafile.outputs)[0]].imports.map(value => value.path), ['react/jsx-runtime'])
  })
}

test('uncertain historical attempt takes priority over a submitted package label', () => {
  const html = renderToStaticMarkup(React.createElement(Publisher, {
    platform: 'douyin', packageStatus: 'submitted', initialJob: { status: 'failed', result: { submission_uncertain: true } },
  }))
  assert.match(html, /历史提交结果仍不确定/)
  assert.doesNotMatch(html, /已公开发布|历史提交回执已保留/)
})

test('historical receipts never claim public publication', () => {
  const html = renderToStaticMarkup(React.createElement(Publisher, {
    platform: 'kuaishou', initialJob: { status: 'submitted' },
  }))
  assert.match(html, /历史提交回执已保留，不代表已公开发布/)
  assert.doesNotMatch(html, /<button|\/api\//)
})
