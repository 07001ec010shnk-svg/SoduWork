import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { createComponentHarness } from './helpers/react-harness.mjs'
import { translateText as tx } from '../src/i18n.js'

const source = new URL('../src/screens/Users.jsx', import.meta.url)
const textOf = node => React.Children.toArray(node?.props?.children)
  .map(child => React.isValidElement(child) ? textOf(child) : String(child)).join('')

const orgs = [{ id: 'team', name: 'Synthetic team' }]
const roles = [{ id: 'member', name: 'Synthetic member' }]
const fixture = [
  { id: 'u-active', username: 'alice', displayName: 'Alice', orgId: 'team', roleId: 'member',
    isActive: true, lastLogin: '2026-11-01T08:00:00Z' },
  { id: 'u-inactive', username: 'testorg', displayName: 'Test Org', orgId: 'team', roleId: 'member',
    isActive: false, lastLogin: null },
  { id: 'u-disabled', username: 'bob', displayName: 'Bob', orgId: 'team', roleId: 'member',
    isActive: false, lastLogin: '2026-10-01T08:00:00Z' },
  { id: 'u-qa', username: 'qatest001', displayName: 'QA runner', orgId: 'team', roleId: 'member',
    isActive: true, lastLogin: '2026-11-02T08:00:00Z' },
]

const accounts = page => page.nodes(node => node.type === 'td' && node.props.className === 'mono').map(textOf)

const statusDot = page => page.nodes(node => node.type === 'span' && node.props.className?.startsWith('status-dot'))

// The harness walks the element tree without invoking function components, so
// the stub keeps its props reachable on the element itself.
const FilterSelectStub = (props) => React.createElement('select', {
  'aria-label': props['aria-label'], value: props.value,
  className: props.className, onChange: (event) => props.onChange(event),
}, React.Children.toArray(props.children))

const mount = async (t, list = fixture) => {
  const api = { users: async () => list, orgs: async () => orgs, roles: async () => roles }
  const page = await createComponentHarness(source, {
    dependencies: {
      '../api.js': { api },
      '../viewport-height.js': { useViewportHeight: () => undefined },
      './admin-access-pages.css': {},
      '../FilterControls.jsx': {
        FilterInput: () => null,
        FilterSelect: FilterSelectStub,
      },
      // Users resolves three catalogs synchronously; the selector keys on the
      // api call inside each useAsync closure.
      '../ui.jsx': {
        useAsync: (fn) => {
          const src = fn.toString()
          const data = src.includes('api.users()') ? list : src.includes('api.orgs()') ? orgs : roles
          return { data, loading: false, error: null, reload: () => {} }
        },
        Loading: () => null, Icon: () => null, Modal: () => null,
        Confirm: () => null, SelectMenu: () => null, toast: () => {},
      },
    },
  })
  page.render()
  await page.flush()
  t.after(() => page.unmount())
  return page
}

function setStatus(page, value) {
  const select = page.nodes(node => node.type === FilterSelectStub && node.props['aria-label'] === tx('状态'))[0]
  assert.ok(select, 'the filter bar exposes a status select')
  page.act(() => select.props.onChange({ target: { value } }))
  page.flush()
}

test('user states distinguish never-activated from disabled accounts', async t => {
  const page = await mount(t)
  const labels = statusDot(page).map(node => textOf(node).trim())
  assert.deepEqual(labels, [tx('启用'), tx('未激活'), tx('已停用'), tx('启用')])
})

test('state tooltips explain what stopping an account means', async t => {
  const page = await mount(t)
  const dots = statusDot(page)
  assert.equal(dots[0].props.title, undefined, 'active accounts need no hint')
  assert.equal(dots[1].props.title, tx('从未登录，账号不可用'))
  assert.equal(dots[2].props.title, tx('登录过之后被停用，账号不可用'))
})

test('the disabled summary explains the shared stop semantics', async t => {
  const page = await mount(t)
  const stat = page.nodes(node => node.props.className?.split(/\s+/).includes('stat')
    && node.props.title === tx('停用账号不可登录；从未登录的为未激活账号'))[0]
  assert.ok(stat, 'the disabled stat carries the semantics hint')
})

test('test accounts are flagged by the username heuristic and plain users stay unflagged', async t => {
  const page = await mount(t)
  const flagged = accounts(page)
  assert.ok(flagged[1].includes(tx('测试')) && flagged[1].includes('testorg'), 'testorg is flagged')
  assert.ok(flagged[3].includes(tx('测试')) && flagged[3].includes('qatest001'), 'qatest001 is flagged')
  assert.ok(!flagged[0].includes(tx('测试')) && flagged[0].includes('alice'), 'alice stays unflagged')
  assert.ok(!flagged[2].includes(tx('测试')) && flagged[2].includes('bob'), 'bob stays unflagged')
})

test('the status filter narrows the list to the selected state', async t => {
  const page = await mount(t)
  setStatus(page, 'inactive')
  assert.deepEqual(accounts(page).map(a => a.trim().replace(tx('测试'), '').trim()), ['testorg'])
  setStatus(page, 'disabled')
  assert.deepEqual(accounts(page).map(a => a.trim()), ['bob'])
  setStatus(page, '')
  assert.equal(accounts(page).length, fixture.length, 'clearing the filter restores every row')
})

test('the test-accounts filter narrows the list to flagged accounts', async t => {
  const page = await mount(t)
  const checkbox = page.nodes(node => node.type === 'input' && node.props.type === 'checkbox')[0]
  assert.ok(checkbox, 'the filter bar exposes a test-accounts toggle')
  page.act(() => checkbox.props.onChange({ target: { checked: true } }))
  page.flush()
  assert.deepEqual(accounts(page).map(a => a.trim().replace(tx('测试'), '').trim()).sort(), ['qatest001', 'testorg'])
  page.act(() => checkbox.props.onChange({ target: { checked: false } }))
  page.flush()
  assert.equal(accounts(page).length, fixture.length, 'clearing the toggle restores every row')
})
