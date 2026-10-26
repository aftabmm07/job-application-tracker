import assert from 'node:assert/strict'
import { after, afterEach, beforeEach, test } from 'node:test'
import { act, createElement } from 'react'
import { JSDOM } from 'jsdom'
import { installJsxLoader } from './helpers/jsxLoader.js'

const jsxLoader = installJsxLoader()
const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost:5173/profile' })
const originalGlobals = new Map()
for (const [name, value] of Object.entries({
  window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
  HTMLElement: dom.window.HTMLElement, Node: dom.window.Node, IS_REACT_ACT_ENVIRONMENT: true,
})) {
  originalGlobals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true })
}
dom.window.fetch = (...args) => globalThis.fetch(...args)
dom.window.XMLHttpRequest = class { constructor() { throw new Error('Network forbidden in profile tests') } }
dom.window.WebSocket = class { constructor() { throw new Error('Network forbidden in profile tests') } }

const { createRoot } = await import('react-dom/client')
const { BrowserRouter } = await import('react-router-dom')
const { default: App } = await import('../src/App.jsx')

let root
let container
let calls
const csrf = 'c'.repeat(64)

const person = (id) => ({ id, email: `person${id}@example.com` })
const profile = (id, changes = {}) => ({
  email: person(id).email, full_name: null, professional_headline: null, location: null,
  linkedin_url: null, github_url: null, portfolio_url: null, ...changes,
})

function reply(status, body) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status, headers: status === 204 ? {} : { 'Content-Type': 'application/json' },
  })
}

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function network(t, handler) {
  t.mock.method(globalThis, 'fetch', (url, options = {}) => {
    calls.push({ url, options })
    return handler(url, options)
  })
}

function standardNetwork(t, data = profile(1), overrides = {}) {
  network(t, (url, options) => {
    if (overrides[url]) return overrides[url](options)
    if (url === '/auth/me') return Promise.resolve(reply(200, person(1)))
    if (url === '/applications') return Promise.resolve(reply(200, []))
    if (url === '/auth/profile') return Promise.resolve(reply(200, data))
    if (url === '/auth/csrf') return Promise.resolve(reply(200, { csrf_token: csrf }))
    assert.fail(`Unexpected request: ${url}`)
  })
}

async function mount() {
  await act(async () => {
    root.render(createElement(BrowserRouter, { window: dom.window }, createElement(App)))
  })
}

async function click(element) {
  assert.ok(element)
  await act(async () => {
    element.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }))
  })
}

async function change(id, value) {
  const input = container.querySelector(`#profile-${id}`)
  assert.ok(input)
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, value)
    input.dispatchEvent(new window.Event('input', { bubbles: true }))
  })
}

async function submit() {
  await act(async () => {
    container.querySelector('.profile-form').dispatchEvent(
      new window.Event('submit', { bubbles: true, cancelable: true }))
  })
}

beforeEach((t) => {
  window.history.replaceState(null, '', '/profile')
  document.body.innerHTML = '<div id="root"></div>'
  container = document.getElementById('root')
  root = createRoot(container)
  calls = []
  t.mock.method(globalThis, 'fetch', () => assert.fail('Unexpected network request'))
})

afterEach(async () => {
  await act(async () => { root.unmount() })
})

after(() => {
  jsxLoader.deregister()
  dom.window.close()
  for (const [name, descriptor] of originalGlobals) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor)
    else delete globalThis[name]
  }
})

test('protected navigation and complete profile display', async (t) => {
  standardNetwork(t, profile(1, {
    full_name: 'Ada Lovelace', professional_headline: 'Software engineer',
    location: 'Berlin', linkedin_url: 'https://linkedin.com/in/ada',
    github_url: 'https://github.com/ada', portfolio_url: 'https://ada.example.com',
  }))
  await mount()
  assert.equal(window.location.pathname, '/profile')
  assert.equal(container.querySelector('a[href="/profile"]').getAttribute('aria-current'), 'page')
  assert.match(container.textContent, /Ada Lovelace/)
  assert.match(container.textContent, /Software engineer/)
  assert.match(container.textContent, /Berlin/)
  assert.match(container.textContent, /person1@example.com/)
  assert.match(container.textContent, /Verified/)
  assert.match(container.querySelector('.profile-avatar').textContent, /AL/)
  assert.equal(container.querySelectorAll('.profile-details a[target="_blank"][rel="noopener noreferrer"]').length, 3)
  assert.equal(calls.filter(({ url }) => url === '/auth/profile').length, 1)
})

test('unsafe link returned by an unexpected response is never rendered as a link', async (t) => {
  standardNetwork(t, profile(1, { portfolio_url: 'javascript:alert(1)' }))
  await mount()
  assert.equal(container.querySelector('.profile-details a[href^="javascript:"]'), null)
  assert.match(container.textContent, /Not added yet/)
})

test('new account shows empty state and read-only email while editing', async (t) => {
  standardNetwork(t)
  await mount()
  assert.match(container.textContent, /Make this space yours/)
  await click(container.querySelector('.profile-header button'))
  assert.equal(document.activeElement.id, 'profile-full_name')
  assert.equal(container.querySelector('#profile-email'), null)
  assert.match(container.textContent, /email cannot be changed/)
  await change('full_name', 'Unsaved Name')
  await click([...container.querySelectorAll('.profile-form button')].find((button) => button.textContent === 'Cancel'))
  assert.equal(container.textContent.includes('Unsaved Name'), false)
  assert.equal(calls.filter(({ url, options }) => url === '/auth/profile' && options.method === 'PATCH').length, 0)
})

test('save sends only changed fields with same-origin cookie and fresh CSRF', async (t) => {
  let savedPayload
  standardNetwork(t, profile(1), {
    '/auth/profile': (options) => {
      if (options.method !== 'PATCH') return Promise.resolve(reply(200, profile(1)))
      savedPayload = JSON.parse(options.body)
      assert.equal(options.credentials, 'same-origin')
      assert.equal(options.headers['X-CSRF-Token'], csrf)
      assert.equal(options.headers['Content-Type'], 'application/json')
      return Promise.resolve(reply(200, profile(1, { full_name: 'Ada Lovelace', github_url: 'https://github.com/ada' })))
    },
  })
  await mount()
  await click(container.querySelector('.profile-header button'))
  await change('full_name', '  Ada Lovelace  ')
  await change('github_url', 'https://github.com/ada')
  await submit()
  assert.deepEqual(savedPayload, { full_name: 'Ada Lovelace', github_url: 'https://github.com/ada' })
  assert.match(container.textContent, /Profile saved successfully/)
  assert.match(container.textContent, /Ada Lovelace/)
})

test('invalid HTTPS URL is shown inline and never submitted', async (t) => {
  standardNetwork(t)
  await mount()
  await click(container.querySelector('.profile-header button'))
  await change('portfolio_url', 'http://localhost/private')
  await submit()
  assert.equal(container.querySelector('#profile-portfolio_url').getAttribute('aria-invalid'), 'true')
  assert.match(container.querySelector('#profile-portfolio_url-error').textContent, /public HTTPS URL/)
  assert.equal(calls.filter(({ url }) => url === '/auth/csrf').length, 0)
})

test('pending save blocks duplicates and a temporary failure can be retried', async (t) => {
  const firstSave = deferred()
  let writes = 0
  standardNetwork(t, profile(1), {
    '/auth/profile': (options) => {
      if (options.method !== 'PATCH') return Promise.resolve(reply(200, profile(1)))
      writes++
      return writes === 1 ? firstSave.promise : Promise.resolve(reply(200, profile(1, { full_name: 'Ada' })))
    },
  })
  await mount()
  await click(container.querySelector('.profile-header button'))
  await change('full_name', 'Ada')
  await submit()
  await submit()
  assert.equal(writes, 1)
  assert.equal(container.querySelector('.profile-form button[type="submit"]').disabled, true)
  await act(async () => { firstSave.resolve(reply(503, { detail: 'private storage detail' })) })
  assert.match(container.querySelector('.profile-form [role="alert"]').textContent, /Unable to save/)
  assert.equal(container.textContent.includes('private storage detail'), false)
  await submit()
  assert.equal(writes, 2)
  assert.match(container.textContent, /Profile saved successfully/)
})

test('loading, temporary error, retry, and server validation feedback', async (t) => {
  const pending = deferred()
  let reads = 0
  standardNetwork(t, profile(1), {
    '/auth/profile': (options) => {
      if (options.method === 'PATCH') return Promise.resolve(reply(422, { detail: 'Invalid authentication request' }))
      reads++
      return reads === 1 ? pending.promise : Promise.resolve(reply(200, profile(1)))
    },
  })
  await mount()
  assert.match(container.textContent, /Loading your profile/)
  await act(async () => { pending.resolve(reply(503, { detail: 'private server detail' })) })
  assert.match(container.textContent, /Unable to load your profile/)
  assert.equal(container.textContent.includes('private server detail'), false)
  await click(container.querySelector('.error-panel button'))
  await click(container.querySelector('.profile-header button'))
  await change('full_name', 'Ada')
  await submit()
  assert.match(container.querySelector('.profile-form [role="alert"]').textContent, /check the profile fields/)
})

test('profile 401 ends the session and hides private details', async (t) => {
  standardNetwork(t, profile(1), {
    '/auth/profile': () => Promise.resolve(reply(401, { detail: 'Authentication required' })),
  })
  await mount()
  assert.equal(window.location.pathname, '/login')
  assert.equal(container.textContent.includes('person1@example.com'), false)
})

test('in-flight profile from old account cannot appear after logout and account switch', async (t) => {
  const oldProfile = deferred()
  let account = 1
  network(t, (url, options) => {
    if (url === '/auth/me') return Promise.resolve(reply(200, person(account)))
    if (url === '/applications') return Promise.resolve(reply(200, []))
    if (url === '/auth/profile') return account === 1 ? oldProfile.promise : Promise.resolve(reply(200, profile(2, { full_name: 'New Account' })))
    if (url === '/auth/csrf') return Promise.resolve(reply(200, { csrf_token: csrf }))
    if (url === '/auth/logout') { account = 2; return Promise.resolve(reply(204)) }
    if (url === '/auth/login') return Promise.resolve(reply(200, person(2)))
    assert.fail(`Unexpected request: ${url} ${options?.method || ''}`)
  })
  await mount()
  assert.match(container.textContent, /Loading your profile/)
  await click(container.querySelector('.logout-button'))
  assert.equal(window.location.pathname, '/login')
  const loginForm = container.querySelector('#login-email').form
  await act(async () => {
    loginForm.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }))
  })
  await click(container.querySelector('a[href="/profile"]'))
  assert.match(container.textContent, /New Account/)
  await act(async () => { oldProfile.resolve(reply(200, profile(1, { full_name: 'Old Account' }))) })
  assert.equal(container.textContent.includes('Old Account'), false)
})
