import assert from 'node:assert/strict'
import test from 'node:test'
import { createComponentHarness } from './helpers/react-harness.mjs'

test('stale publication changes stop until latest catalog is loaded', async t => {
  let reads = 0
  const writes = []
  const page = await createComponentHarness(new URL('../src/screens/AutomationTemplates.jsx', import.meta.url), {
    dependencies: {
      '../api.js': { api: {
        automationTemplates: async () => ({ items: [{ id: 'report', name: 'Report', enabled: true, every: 'daily' }], revision: `v${++reads}` }),
        saveAutomationTemplates: async (items, revision) => { writes.push(revision); throw Object.assign(new Error('Conflict'), { status: 409 }) },
      } },
      '../ui.jsx': { Confirm: () => null, Icon: () => null, Loading: () => null, Modal: () => null, SelectMenu: () => null, toast: () => {} },
      '../components/LocalesEditor.jsx': () => null, './automation-templates.css': {},
    },
  })
  t.after(() => page.unmount())
  page.render(); await page.flush()
  const toggle = () => page.nodes(n => n.props.className?.includes('at-toggle'))[0]
  await toggle().props.onClick(); page.render()
  assert.deepEqual(writes, ['v1'])
  await toggle().props.onClick()
  assert.equal(writes.length, 1)
  const alert = page.nodes(n => n.props.role === 'alert')[0]
  assert.ok(alert)
  const reload = page.nodes(n => n.type === 'button' && n.props.className === 'btn btn-ghost')[0]
  reload.props.onClick(); await page.flush()
  await toggle().props.onClick()
  assert.deepEqual(writes, ['v1', 'v2'])
})
