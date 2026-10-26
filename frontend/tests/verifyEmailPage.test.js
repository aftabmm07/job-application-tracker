import assert from 'node:assert/strict'
import { after, afterEach, beforeEach, test } from 'node:test'
import { act, createElement, StrictMode, useLayoutEffect } from 'react'
import { JSDOM } from 'jsdom'
import { installJsxLoader } from './helpers/jsxLoader.js'

const jsxLoader = installJsxLoader()

// No scripts or external resources are enabled in this DOM. All fetches below
// are replaced with mocks, including unexpected requests (which fail tests).
const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost:5173/verify-email' })
const originalGlobals = new Map()
for (const [name, value] of Object.entries({
  window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
  HTMLElement: dom.window.HTMLElement, Node: dom.window.Node, IS_REACT_ACT_ENVIRONMENT: true,
})) {
  originalGlobals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true })
}
dom.window.fetch = (...args) => globalThis.fetch(...args)
dom.window.XMLHttpRequest = class { constructor() { throw new Error('Network access forbidden in DOM tests') } }
dom.window.WebSocket = class { constructor() { throw new Error('Network access forbidden in DOM tests') } }

const { createRoot } = await import('react-dom/client')
const { BrowserRouter } = await import('react-router-dom')
const { default: App } = await import('../src/App.jsx')

const token = 'a'.repeat(43)
const csrf = 'b'.repeat(64)
const originalLink = `/verify-email#token=${token}`
let root
let container
let calls
let replay

function response(status = 200, body = {}) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function ReplayProbe() {
  useLayoutEffect(() => {
    replay.setups++
    return () => { replay.cleanups++ }
  }, [])
  return null
}

function tree() {
  return createElement(StrictMode, null,
    createElement(BrowserRouter, null, createElement(ReplayProbe), createElement(App)))
}

async function mount() {
  await act(async () => { root.render(tree()) })
}

function mockNetwork(t, handler) {
  t.mock.method(globalThis, 'fetch', (url, options) => {
    // Every actual network attempt observes a cleaned address, even bootstrap.
    assert.equal(window.location.hash, '')
    assert.equal(window.location.search, '')
    assert.equal(String(url).includes(token), false)
    calls.push({ url, options })
    return handler(url, options)
  })
}

function verificationCalls() {
  return calls.filter(({ url }) => url === '/auth/verify-email')
}

function expectPrivateDOM() {
  assert.equal(container.innerHTML.includes(token), false)
  assert.equal(window.localStorage.length, 0)
  assert.equal(window.sessionStorage.length, 0)
}

beforeEach((t) => {
  window.history.replaceState({ key: 'test-key', idx: 0 }, '', originalLink)
  document.body.innerHTML = '<div id="root"></div>'
  container = document.getElementById('root')
  root = createRoot(container)
  calls = []
  replay = { setups: 0, cleanups: 0 }
  t.mock.method(globalThis, 'fetch', (url, options) => {
    calls.push({ url, options })
    assert.fail('Unexpected network request')
  })
  window.localStorage.clear()
  window.sessionStorage.clear()
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

test('real StrictMode replay and rerenders issue exactly one POST; show loading then success', async (t) => {
  const bootstrap = deferred()
  const verification = deferred()
  mockNetwork(t, (url) => {
    if (url === '/auth/csrf') return bootstrap.promise
    if (url === '/auth/verify-email') return verification.promise
    assert.fail('Unexpected endpoint')
  })
  await mount()
  assert.equal(replay.setups, 2)
  assert.equal(replay.cleanups, 1)
  assert.equal(window.location.pathname, '/verify-email')
  assert.equal(window.history.state.key, 'test-key')
  assert.deepEqual(calls.map(({ url }) => url), ['/auth/csrf'])
  assert.match(container.querySelector('[role="status"]').textContent, /Please wait/)
  assert.equal(container.querySelector('section').getAttribute('aria-busy'), 'true')
  assert.equal(document.activeElement, container.querySelector('h1'))

  await act(async () => { bootstrap.resolve(response(200, { csrf_token: csrf })) })
  assert.equal(verificationCalls().length, 1)
  const { options } = verificationCalls()[0]
  assert.equal(options.method, 'POST')
  assert.equal(options.credentials, 'same-origin')
  assert.equal(options.headers['X-CSRF-Token'], csrf)
  assert.equal(options.headers['Content-Type'], 'application/json')
  assert.deepEqual(JSON.parse(options.body), { token })
  await act(async () => { root.render(tree()) })
  assert.equal(calls.length, 2)
  await act(async () => { verification.resolve(response()) })
  assert.match(container.querySelector('[role="status"]').textContent, /email is verified/)
  assert.equal(container.querySelector('section').getAttribute('aria-busy'), 'false')
  assert.equal(window.location.pathname, '/verify-email') // No automatic login.
  expectPrivateDOM()
})

test('true remount of the cleaned URL does not replay a pending verification', async (t) => {
  const verification = deferred()
  mockNetwork(t, (url) => url === '/auth/csrf'
    ? Promise.resolve(response(200, { csrf_token: csrf })) : verification.promise)
  await mount()
  assert.equal(verificationCalls().length, 1)
  await act(async () => { root.unmount() })
  root = createRoot(container)
  await mount()
  assert.match(container.querySelector('[role="alert"]').textContent, /invalid, expired, or already used/)
  assert.equal(calls.length, 2)
  // An old request finishing cannot change a newly mounted page's invalid state.
  await act(async () => { verification.resolve(response()) })
  assert.match(container.querySelector('[role="alert"]').textContent, /invalid, expired, or already used/)
  assert.equal(verificationCalls().length, 1)
  expectPrivateDOM()
})

test('explicitly reopening the original link on a new mount makes a fresh attempt, not an automatic retry', async (t) => {
  let posts = 0
  mockNetwork(t, (url) => {
    if (url === '/auth/csrf') return Promise.resolve(response(200, { csrf_token: csrf }))
    posts++
    return Promise.resolve(posts === 1 ? response() : response(400, { detail: token }))
  })
  await mount()
  await act(async () => { root.unmount() })
  window.history.replaceState({ key: 'reopened', idx: 0 }, '', originalLink)
  root = createRoot(container)
  await mount()
  assert.equal(posts, 2)
  assert.equal(calls.length, 4)
  assert.match(container.querySelector('[role="alert"]').textContent, /invalid, expired, or already used/)
  expectPrivateDOM()
})

for (const [name, url] of [
  ['missing', '/verify-email'], ['empty', '/verify-email#token='],
  ['short', '/verify-email#token=bad'], ['query only', `/verify-email?token=${token}`],
  ['duplicate', `${originalLink}&token=${token}`], ['extra parameter', `${originalLink}&extra=1`],
]) {
  test(`${name} token: DOM shows invalid link and no requests occur`, async () => {
    window.history.replaceState({ key: 'invalid', idx: 0 }, '', url)
    await mount()
    assert.equal(window.location.hash, '')
    assert.equal(window.location.search, '')
    assert.match(container.querySelector('[role="alert"]').textContent, /invalid, expired, or already used/)
    assert.equal(calls.length, 0)
    expectPrivateDOM()
  })
}

for (const failure of ['HTTP error', 'invalid CSRF response', 'network error']) {
  test(`CSRF bootstrap ${failure}: temporary error, no verification submission`, async (t) => {
    mockNetwork(t, () => {
      if (failure === 'network error') return Promise.reject(new Error(token))
      return Promise.resolve(failure === 'HTTP error' ? response(503, { detail: token })
        : response(200, { csrf_token: 'invalid' }))
    })
    await mount()
    assert.equal(calls.length, 1)
    assert.equal(verificationCalls().length, 0)
    assert.match(container.querySelector('[role="alert"]').textContent, /could not be confirmed/)
    expectPrivateDOM()
  })
}

for (const status of [400, 422, 403, 429, 500, 503]) {
  test(`verification HTTP ${status}: safe DOM error and no retry`, async (t) => {
    mockNetwork(t, (url) => Promise.resolve(url === '/auth/csrf'
      ? response(200, { csrf_token: csrf }) : response(status, { detail: token })))
    await mount()
    const message = container.querySelector('[role="alert"]').textContent
    assert.match(message, status === 400 || status === 422 ? /invalid, expired, or already used/ : /could not be confirmed/)
    assert.equal(verificationCalls().length, 1)
    await act(async () => { root.render(tree()) })
    assert.equal(calls.length, 2)
    expectPrivateDOM()
  })
}

test('verification network failure shows temporary error without raw exception data', async (t) => {
  mockNetwork(t, (url) => url === '/auth/csrf'
    ? Promise.resolve(response(200, { csrf_token: csrf })) : Promise.reject(new Error(token)))
  await mount()
  assert.match(container.querySelector('[role="alert"]').textContent, /could not be confirmed/)
  assert.equal(verificationCalls().length, 1)
  expectPrivateDOM()
})

test('login link performs client-side navigation to the existing login page', async (t) => {
  mockNetwork(t, (url) => {
    if (url === '/auth/csrf') return Promise.resolve(response(200, { csrf_token: csrf }))
    if (url === '/auth/verify-email') return Promise.resolve(response())
    if (url === '/auth/me') return Promise.resolve(response(401))
    assert.fail('Unexpected endpoint')
  })
  await mount()
  const link = container.querySelector('a[href="/login"]')
  assert.equal(link.textContent, 'Go to sign in')
  await act(async () => { link.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })) })
  assert.equal(window.location.pathname, '/login')
  assert.equal(container.querySelector('h1').textContent, 'Welcome back')
  assert.equal(container.querySelector('input[type="password"]').value, '')
  assert.equal(verificationCalls().length, 1)
  expectPrivateDOM()
})

async function enterEmail(value) {
  const input = container.querySelector('#resend-email')
  assert.ok(input)
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, value)
    input.dispatchEvent(new window.Event('input', { bubbles: true }))
  })
  return input
}

async function submitResend() {
  await act(async () => {
    container.querySelector('.resend-form').dispatchEvent(
      new window.Event('submit', { bubbles: true, cancelable: true }))
  })
}

test('invalid verification link offers email-only resend with generic acknowledgement', async (t) => {
  window.history.replaceState(null, '', '/verify-email#token=bad')
  mockNetwork(t, (url) => {
    if (url === '/auth/csrf') return Promise.resolve(response(200, { csrf_token: csrf }))
    if (url === '/auth/resend-verification') return Promise.resolve(response(202, {}))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  assert.equal(calls.length, 0)
  assert.ok(container.querySelector('a[href="/login"]'))
  await enterEmail(' PERSON@EXAMPLE.COM ')
  await submitResend()
  assert.deepEqual(calls.map(({ url }) => url), ['/auth/csrf', '/auth/resend-verification'])
  const request = calls[1].options
  assert.equal(request.method, 'POST')
  assert.equal(request.credentials, 'same-origin')
  assert.equal(request.headers['Content-Type'], 'application/json')
  assert.equal(request.headers['X-CSRF-Token'], csrf)
  assert.deepEqual(JSON.parse(request.body), { email: 'PERSON@EXAMPLE.COM' })
  assert.match(container.querySelector('.resend-form [role="status"]').textContent,
    /If this address needs verification, instructions will be sent when available/)
  assert.equal(request.body.includes('token'), false)
  expectPrivateDOM()
})

test('resend rejects invalid email without network access', async () => {
  window.history.replaceState(null, '', '/verify-email')
  await mount()
  await enterEmail('not-an-email')
  await submitResend()
  assert.match(container.querySelector('.resend-form [role="alert"]').textContent, /valid email/)
  assert.equal(document.activeElement.id, 'resend-email')
  assert.equal(calls.length, 0)
})

for (const failedStep of ['csrf', 'request', 'server']) {
  test(`resend ${failedStep} failure is retryable and does not disclose server detail`, async (t) => {
    window.history.replaceState(null, '', '/verify-email')
    let failures = 0
    mockNetwork(t, (url) => {
      if (url === '/auth/csrf') return Promise.resolve(response(200, { csrf_token: csrf }))
      if (url === '/auth/resend-verification' && failures++ === 0) {
        return failedStep === 'request' ? Promise.reject(new Error(token)) : Promise.resolve(response(503, { detail: token }))
      }
      return Promise.resolve(response(202, {}))
    })
    if (failedStep === 'csrf') {
      mockNetwork(t, (url) => {
        if (url === '/auth/csrf' && failures++ === 0) return Promise.reject(new Error(token))
        return Promise.resolve(url === '/auth/csrf' ? response(200, { csrf_token: csrf }) : response(202, {}))
      })
    }
    await mount()
    await enterEmail('person@example.com')
    await submitResend()
    assert.match(container.querySelector('.resend-form [role="alert"]').textContent, /try again/)
    expectPrivateDOM()
    await submitResend()
    assert.match(container.querySelector('.resend-form [role="status"]').textContent, /If this address needs verification/)
  })
}

test('resend disables and guards duplicate submissions while pending', async (t) => {
  window.history.replaceState(null, '', '/verify-email')
  const pending = deferred()
  mockNetwork(t, (url) => Promise.resolve(url === '/auth/csrf'
    ? response(200, { csrf_token: csrf }) : pending.promise).then((result) => result))
  await mount()
  await enterEmail('person@example.com')
  await act(async () => {
    const form = container.querySelector('.resend-form')
    form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }))
    form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }))
  })
  assert.equal(calls.filter(({ url }) => url === '/auth/resend-verification').length, 1)
  assert.equal(container.querySelector('.resend-form button').disabled, true)
  await act(async () => { pending.resolve(response(202, {})) })
  assert.equal(container.querySelector('.resend-form button').disabled, false)
})

test('registration success offers resend using the entered email', async (t) => {
  window.history.replaceState(null, '', '/register')
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(response(401))
    if (url === '/auth/csrf') return Promise.resolve(response(200, { csrf_token: csrf }))
    if (url === '/auth/register') return Promise.resolve(response(202, { message: 'Registration received.' }))
    if (url === '/auth/resend-verification') return Promise.resolve(response(202, {}))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  const email = container.querySelector('#register-email')
  const password = container.querySelector('#register-password')
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(email, 'person@example.com')
    email.dispatchEvent(new window.Event('input', { bubbles: true }))
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(password, 'a secure password of sufficient length')
    password.dispatchEvent(new window.Event('input', { bubbles: true }))
  })
  await act(async () => {
    container.querySelector('#register-email').form.dispatchEvent(
      new window.Event('submit', { bubbles: true, cancelable: true }))
  })
  assert.ok(container.querySelector('.resend-form'))
  assert.equal(container.querySelector('#resend-email').value, 'person@example.com')
  await submitResend()
  assert.equal(calls.filter(({ url }) => url === '/auth/resend-verification').length, 1)
})

async function enterAuthValue(selector, value) {
  await act(async () => {
    const input = container.querySelector(selector)
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, value)
    input.dispatchEvent(new window.Event('input', { bubbles: true }))
  })
}

async function submitRegistration() {
  await act(async () => {
    container.querySelector('#register-email').form.dispatchEvent(
      new window.Event('submit', { bubbles: true, cancelable: true }))
  })
}

test('login links to registration and registration links back without reloading', async (t) => {
  window.history.replaceState(null, '', '/login')
  mockSessionFetch(t, (url) => {
    assert.equal(url, '/auth/me')
    return Promise.resolve(response(401))
  })
  await mount()
  const createLink = container.querySelector('a[href="/register"]')
  assert.equal(createLink.textContent, 'Create account')
  await sessionClick(createLink)
  assert.equal(window.location.pathname, '/register')
  assert.equal(container.querySelector('a[href="/login"]').textContent, 'Sign in')
  assert.match(container.querySelector('.auth-card').textContent, /15–128 Unicode characters/)
})

for (const [name, password, allowed] of [
  ['14 characters', 'a'.repeat(14), false],
  ['15 Unicode characters', '🙂'.repeat(15), true],
  ['129 Unicode characters', 'é'.repeat(129), false],
]) {
  test(`registration password boundary: ${name}`, async (t) => {
    window.history.replaceState(null, '', '/register')
    mockSessionFetch(t, (url) => {
      if (url === '/auth/me') return Promise.resolve(response(401))
      if (url === '/auth/csrf') return Promise.resolve(response(200, { csrf_token: csrf }))
      if (url === '/auth/register') return Promise.resolve(response(202, { message: 'Registration received.' }))
      assert.fail(`Unexpected endpoint: ${url}`)
    })
    await mount()
    await enterAuthValue('#register-email', 'person@example.com')
    await enterAuthValue('#register-password', password)
    const input = container.querySelector('#register-password')
    assert.match(input.getAttribute('aria-describedby'), /register-password-hint/)
    await submitRegistration()
    const posts = calls.filter(({ url }) => url === '/auth/register')
    assert.equal(posts.length, allowed ? 1 : 0)
    if (allowed) {
      assert.equal(JSON.parse(posts[0].options.body).password, password)
      assert.equal(container.querySelector('#register-password').value, '')
    } else {
      assert.equal(input.getAttribute('aria-invalid'), 'true')
      assert.match(container.querySelector('#register-password-error').textContent, /15–128/)
      assert.equal(calls.some(({ url }) => url === '/auth/csrf'), false)
    }
  })
}

test('registration preserves spaces and blocks a second submission while pending', async (t) => {
  window.history.replaceState(null, '', '/register')
  const pending = deferred()
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(response(401))
    if (url === '/auth/csrf') return Promise.resolve(response(200, { csrf_token: csrf }))
    if (url === '/auth/register') return pending.promise
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  await enterAuthValue('#register-email', 'person@example.com')
  const password = '  fifteen characters 🙂  '
  await enterAuthValue('#register-password', password)
  act(() => {
    const form = container.querySelector('#register-email').form
    form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }))
    form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }))
  })
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(calls.filter(({ url }) => url === '/auth/register').length, 1)
  assert.equal(container.querySelector('.auth-card button[type="submit"]').disabled, true)
  assert.equal(JSON.parse(calls.find(({ url }) => url === '/auth/register').options.body).password, password)
  await act(async () => { pending.resolve(response(202, { message: 'Registration received.' })) })
  assert.equal(container.querySelector('.auth-card button[type="submit"]').disabled, false)
})

// Session cases share this file's single JSDOM instance. Node's no-isolation
// runner loads test modules before executing tests, so a second DOM test module
// would replace global window while the first module is still running.
const person = (id) => ({ id, email: `person${id}@example.com`, is_active: true })
const application = (id, company) => ({
  id, company_name: company, job_title: 'Engineer', location: null,
  status: 'applied', application_date: null, job_description: null,
  created_at: '2026-01-01T00:00:00Z',
})
function sessionReply(status = 200, body = {}) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}
function mockSessionFetch(t, handler) {
  t.mock.method(globalThis, 'fetch', (url, options = {}) => {
    calls.push({ url, options })
    return handler(url, options)
  })
}
async function sessionClick(element) {
  assert.ok(element)
  await act(async () => {
    element.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }))
  })
}
function assertSessionPrivateHidden(...names) {
  for (const name of names) assert.equal(container.textContent.includes(name), false)
}
function sessionTest(name, run) {
  test(name, async (t) => {
    window.history.replaceState(null, '', '/applications')
    await run(t)
  })
}
// jsdom has no native HTML dialog behavior; these methods model opening and
// closing for the existing application's focused confirmation-dialog tests.
dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true }
dom.window.HTMLDialogElement.prototype.close = function () { this.open = false }
sessionTest('confirmed /auth/me 401 redirects to login without fetching applications', async (t) => {
  mockSessionFetch(t, (url) => {
    assert.equal(url, '/auth/me')
    return Promise.resolve(sessionReply(401, { detail: 'Authentication required' }))
  })
  await mount()
  assert.equal(window.location.pathname, '/login')
  assert.match(container.textContent, /Welcome back/)
  assertSessionPrivateHidden('Secret Company')
  assert.equal(calls.some((call) => call.url === '/applications'), false)
})

for (const failure of ['HTTP 503', 'network']) {
  sessionTest(`temporary /auth/me ${failure} hides data, retries, and restores only fresh data`, async (t) => {
    let checks = 0
    mockSessionFetch(t, (url) => {
      if (url === '/auth/me') {
        checks++
        if (checks === 1) return failure === 'network'
          ? Promise.reject(new Error('private server detail'))
          : Promise.resolve(sessionReply(503, { detail: 'private server detail' }))
        return Promise.resolve(sessionReply(200, person(1)))
      }
      if (url === '/applications') return Promise.resolve(sessionReply(200, [application(1, 'Fresh Company')]))
      assert.fail(`Unexpected endpoint: ${url}`)
    })
    await mount()
    assert.equal(window.location.pathname, '/applications')
    assert.match(container.textContent, /Unable to check your session/)
    assertSessionPrivateHidden('private server detail', 'Fresh Company')
    assert.equal(container.querySelector('a[href="/login"]'), null)
    assert.equal(calls.some((call) => call.url === '/applications'), false)
    await sessionClick(container.querySelector('button'))
    assert.match(container.textContent, /Fresh Company/)
    assert.equal(calls.filter((call) => call.url === '/applications').length, 1)
  })
}

sessionTest('protected GET 401 clears auth and never displays an application row', async (t) => {
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(sessionReply(200, person(1)))
    if (url === '/applications') return Promise.resolve(sessionReply(401, { detail: 'Authentication required' }))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  assert.equal(window.location.pathname, '/login')
  assertSessionPrivateHidden('Secret Company')
})

sessionTest('navigation marks the active page and switches to analytics without another data request', async (t) => {
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(sessionReply(200, person(1)))
    if (url === '/applications') return Promise.resolve(sessionReply(200, [application(1, 'Example Company')]))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  assert.equal(container.querySelector('nav[aria-label="Main navigation"] a[aria-current="page"]').getAttribute('href'), '/applications')
  await sessionClick(container.querySelector('nav a[href="/analytics"]'))
  assert.equal(window.location.pathname, '/analytics')
  assert.equal(container.querySelector('nav a[aria-current="page"]').getAttribute('href'), '/analytics')
  assert.match(container.textContent, /Interview rate/)
  assert.match(container.textContent, /Example Company|Status distribution/)
  assert.equal(calls.filter(({ url }) => url === '/applications').length, 1)
})

sessionTest('empty applications state offers the existing Add dialog', async (t) => {
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(sessionReply(200, person(1)))
    if (url === '/applications') return Promise.resolve(sessionReply(200, []))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  assert.match(container.textContent, /No applications yet/)
  await sessionClick(container.querySelector('.empty-state button'))
  assert.equal(container.querySelector('.application-dialog').open, true)
  assert.match(container.querySelector('.application-dialog h2').textContent, /Add new application/)
  assert.equal(calls.filter(({ url }) => url === '/applications').length, 1)
})

sessionTest('analytics loading and error states name the current page and keep retry available', async (t) => {
  const pending = deferred()
  let requests = 0
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(sessionReply(200, person(1)))
    if (url === '/applications') {
      requests++
      return requests === 1 ? pending.promise : Promise.resolve(sessionReply(200, []))
    }
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  await sessionClick(container.querySelector('nav a[href="/analytics"]'))
  assert.match(container.textContent, /Loading analytics/)
  await act(async () => { pending.resolve(sessionReply(503, { detail: 'temporary' })) })
  assert.match(container.textContent, /Unable to load analytics/)
  await sessionClick(container.querySelector('.error-panel button'))
  assert.match(container.textContent, /No applications yet/)
  assert.equal(requests, 2)
})

sessionTest('applications first-load failure offers retry and restores the list', async (t) => {
  let requests = 0
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(sessionReply(200, person(1)))
    if (url === '/applications') {
      requests++
      return Promise.resolve(requests === 1
        ? sessionReply(503, { detail: 'temporary' })
        : sessionReply(200, [application(1, 'Recovered Company')]))
    }
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  assert.match(container.textContent, /Unable to load applications/)
  assert.equal(container.textContent.includes('Recovered Company'), false)
  await sessionClick(container.querySelector('.error-panel button'))
  assert.match(container.textContent, /Recovered Company/)
  assert.equal(requests, 2)
})

sessionTest('search and status filters share one list while summary counts stay global', async (t) => {
  const records = [application(1, 'Alpha Company'),
    { ...application(2, 'Beta Company'), status: 'offer', job_title: 'Designer' }]
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(sessionReply(200, person(1)))
    if (url === '/applications') return Promise.resolve(sessionReply(200, records))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  await enterAuthValue('#application-search', ' beta ')
  await act(async () => {
    const select = container.querySelector('#status-filter')
    select.value = 'offer'
    select.dispatchEvent(new window.Event('change', { bubbles: true }))
  })
  assert.equal(container.querySelectorAll('.application-card').length, 1)
  assert.match(container.querySelector('.application-card').textContent, /Beta Company/)
  assert.equal(container.querySelector('.summary-card dd').textContent, '2')
  assert.match(container.querySelector('.panel-header').textContent, /1 of 2 applications/)
  await sessionClick(container.querySelector('.application-filters button'))
  assert.equal(container.querySelectorAll('.application-card').length, 2)
  assert.equal(calls.filter(({ url }) => url === '/applications').length, 1)
})

sessionTest('protected DELETE 401 clears previously visible data and routes to login', async (t) => {
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(sessionReply(200, person(1)))
    if (url === '/applications') return Promise.resolve(sessionReply(200, [application(1, 'Secret Company')]))
    if (url === '/auth/csrf') return Promise.resolve(sessionReply(200, { csrf_token: csrf }))
    if (url === '/applications/1') return Promise.resolve(sessionReply(401, { detail: 'Authentication required' }))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  assert.match(container.textContent, /Secret Company/)
  await sessionClick(container.querySelector('[aria-label="Delete Engineer at Secret Company"]'))
  await sessionClick(container.querySelector('.delete-dialog .danger-button'))
  assert.equal(window.location.pathname, '/login')
  assertSessionPrivateHidden('Secret Company')
})

sessionTest('logout hides displayed data immediately while the request is pending', async (t) => {
  const logout = deferred()
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(sessionReply(200, person(1)))
    if (url === '/applications') return Promise.resolve(sessionReply(200, [application(1, 'Secret Company')]))
    if (url === '/auth/csrf') return Promise.resolve(sessionReply(200, { csrf_token: csrf }))
    if (url === '/auth/logout') return logout.promise
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  assert.match(container.textContent, /Secret Company/)
  await sessionClick(container.querySelector('.logout-button'))
  assertSessionPrivateHidden('Secret Company')
  assert.match(container.textContent, /Checking your session/)
  await act(async () => { logout.resolve(sessionReply(204)) })
  assert.equal(window.location.pathname, '/login')
})

sessionTest('account switching never shows the previous account after login', async (t) => {
  const oldList = deferred()
  let meCount = 0
  let loggedOut = false
  let csrfCount = 0
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') {
      meCount++
      return Promise.resolve(sessionReply(200, person(1)))
    }
    if (url === '/applications') {
      return loggedOut ? Promise.resolve(sessionReply(200, [application(2, 'New Account Company')])) : oldList.promise
    }
    if (url === '/auth/csrf') {
      csrfCount++
      return Promise.resolve(sessionReply(200, { csrf_token: csrf }))
    }
    if (url === '/auth/logout') { loggedOut = true; return Promise.resolve(sessionReply(204)) }
    if (url === '/auth/login') return Promise.resolve(sessionReply(200, person(2)))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  assert.equal(meCount >= 1, true)
  assertSessionPrivateHidden('Old Account Company')
  await sessionClick(container.querySelector('.logout-button'))
  assert.equal(window.location.pathname, '/login')
  const form = container.querySelector('form')
  await act(async () => { form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })) })
  assert.equal(window.location.pathname, '/applications')
  assert.match(container.textContent, /New Account Company/)
  await act(async () => { oldList.resolve(sessionReply(200, [application(1, 'Old Account Company')])) })
  assertSessionPrivateHidden('Old Account Company')
  assert.equal(csrfCount >= 3, true)
})

async function fillLogin() {
  for (const [id, value] of [
    ['login-email', 'person2@example.com'], ['login-password', 'correct horse battery staple'],
  ]) {
    const input = container.querySelector(`#${id}`)
    await act(async () => {
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, value)
      input.dispatchEvent(new window.Event('input', { bubbles: true }))
    })
  }
}

async function submitLogin() {
  await act(async () => {
    container.querySelector('#login-email').form.dispatchEvent(
      new window.Event('submit', { bubbles: true, cancelable: true }))
  })
}

test('confirmed login with failed CSRF refresh revalidates the rotated session', async (t) => {
  window.history.replaceState(null, '', '/login')
  let csrfCalls = 0
  let meCalls = 0
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') {
      meCalls++
      return Promise.resolve(sessionReply(meCalls === 1 ? 401 : 200, person(2)))
    }
    if (url === '/auth/csrf') {
      csrfCalls++
      return Promise.resolve(csrfCalls === 1
        ? sessionReply(200, { csrf_token: csrf }) : sessionReply(503, { detail: 'private error' }))
    }
    if (url === '/auth/login') return Promise.resolve(sessionReply(200, person(2)))
    if (url === '/applications') return Promise.resolve(sessionReply(200, [application(2, 'New Account Company')]))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  await fillLogin()
  await submitLogin()
  assert.equal(window.location.pathname, '/applications')
  assert.match(container.textContent, /New Account Company/)
  assert.equal(container.textContent.includes('Unable to sign in'), false)
  assert.equal(calls.filter(({ url }) => url === '/auth/login').length, 1)
  assert.equal(csrfCalls, 2)
  assert.equal(meCalls, 2)
})

test('failed login CSRF refresh and temporary session check hide data until retry', async (t) => {
  window.history.replaceState(null, '', '/login')
  let csrfCalls = 0
  let meCalls = 0
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') {
      meCalls++
      return Promise.resolve(meCalls === 1 ? sessionReply(401)
        : meCalls === 2 ? sessionReply(503) : sessionReply(200, person(2)))
    }
    if (url === '/auth/csrf') {
      csrfCalls++
      return Promise.resolve(csrfCalls === 1
        ? sessionReply(200, { csrf_token: csrf }) : sessionReply(503))
    }
    if (url === '/auth/login') return Promise.resolve(sessionReply(200, person(2)))
    if (url === '/applications') return Promise.resolve(sessionReply(200, [application(2, 'Recovered Company')]))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  await fillLogin()
  await submitLogin()
  assert.match(container.textContent, /Unable to check your session/)
  assert.equal(container.textContent.includes('Unable to sign in'), false)
  assertSessionPrivateHidden('Recovered Company')
  assert.equal(calls.filter(({ url }) => url === '/auth/login').length, 1)
  await sessionClick(container.querySelector('.error-panel button'))
  assert.match(container.textContent, /Recovered Company/)
  assert.equal(calls.filter(({ url }) => url === '/auth/login').length, 1)
})

test('invalid credentials remain a confirmed login failure without a refresh', async (t) => {
  window.history.replaceState(null, '', '/login')
  let csrfCalls = 0
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(sessionReply(401))
    if (url === '/auth/csrf') {
      csrfCalls++
      return Promise.resolve(sessionReply(200, { csrf_token: csrf }))
    }
    if (url === '/auth/login') return Promise.resolve(sessionReply(401, { detail: 'Invalid credentials' }))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  await fillLogin()
  await submitLogin()
  assert.equal(window.location.pathname, '/login')
  assert.match(container.querySelector('[role="alert"]').textContent, /Unable to sign in\. Check your email and password\./)
  assert.equal(csrfCalls, 1)
})

test('stale in-flight login cannot restore auth after leaving the page', async (t) => {
  window.history.replaceState(null, '', '/login')
  const pendingLogin = deferred()
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(sessionReply(401))
    if (url === '/auth/csrf') return Promise.resolve(sessionReply(200, { csrf_token: csrf }))
    if (url === '/auth/login') return pendingLogin.promise
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  await fillLogin()
  await act(async () => {
    const form = container.querySelector('#login-email').form
    form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }))
    form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }))
  })
  assert.equal(calls.filter(({ url }) => url === '/auth/login').length, 1)
  window.history.pushState(null, '', '/register')
  await act(async () => { window.dispatchEvent(new window.PopStateEvent('popstate')) })
  assert.equal(window.location.pathname, '/register')
  await act(async () => { pendingLogin.resolve(sessionReply(200, person(2))) })
  assert.equal(window.location.pathname, '/register')
  assertSessionPrivateHidden('New Account Company')
  assert.equal(calls.filter(({ url }) => url === '/applications').length, 0)
})

sessionTest('application details show stored fields as text and close with focus restored', async (t) => {
  const record = {
    ...application(7, 'Example Company'), job_title: 'Senior Engineer',
    location: 'Remote', status: 'interview', application_date: '2026-04-08',
    job_description: 'First line\n<img src=x onerror=alert(1)>',
    created_at: '2026-04-09T12:30:00Z',
  }
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(sessionReply(200, person(1)))
    if (url === '/applications') return Promise.resolve(sessionReply(200, [record]))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  const opener = container.querySelector('[aria-label="View details for Senior Engineer at Example Company"]')
  opener.focus()
  await sessionClick(opener)
  const dialog = container.querySelector('.details-dialog')
  assert.equal(dialog.open, true)
  assert.equal(dialog.getAttribute('aria-labelledby'), 'application-details-heading')
  assert.equal(document.activeElement.textContent, 'Close details')
  for (const value of ['Example Company', 'Senior Engineer', 'Remote', 'interview', '2026', 'First line']) {
    assert.ok(dialog.textContent.includes(value), `Missing displayed value: ${value}`)
  }
  assert.equal(dialog.querySelector('.details-description dd').textContent, record.job_description)
  assert.equal(dialog.querySelector('img'), null)
  assert.deepEqual(calls.map(({ url }) => url), ['/auth/me', '/applications'])
  await sessionClick(dialog.querySelector('button'))
  assert.equal(container.querySelector('.details-dialog'), null)
  assert.equal(document.activeElement, opener)
})

sessionTest('application details show missing optional fields and Escape closes the dialog', async (t) => {
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(sessionReply(200, person(1)))
    if (url === '/applications') return Promise.resolve(sessionReply(200, [application(2, 'Sparse Company')]))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  const opener = container.querySelector('[aria-label="View details for Engineer at Sparse Company"]')
  opener.focus()
  await sessionClick(opener)
  const dialog = container.querySelector('.details-dialog')
  assert.equal(dialog.querySelectorAll('dd').length, 7)
  assert.equal(dialog.textContent.match(/Not provided/g).length, 3)
  await act(async () => { dialog.dispatchEvent(new window.Event('cancel', { bubbles: false, cancelable: true })) })
  assert.equal(container.querySelector('.details-dialog'), null)
  assert.equal(document.activeElement, opener)
  assert.deepEqual(calls.map(({ url }) => url), ['/auth/me', '/applications'])
})

sessionTest('mobile cards show record essentials and reuse details, edit, and delete actions', async (t) => {
  const company = 'Example'.repeat(20)
  const title = 'Senior Engineer'.repeat(12)
  const record = { ...application(9, company), job_title: title, location: 'Remote', status: 'offer' }
  mockSessionFetch(t, (url) => {
    if (url === '/auth/me') return Promise.resolve(sessionReply(200, person(1)))
    if (url === '/applications') return Promise.resolve(sessionReply(200, [record]))
    assert.fail(`Unexpected endpoint: ${url}`)
  })
  await mount()
  const card = container.querySelector('.application-card')
  assert.ok(card)
  assert.equal(card.querySelector('h3').textContent, title)
  assert.equal(card.querySelector('.application-card-company').textContent, company)
  assert.match(card.textContent, /offer/)
  assert.match(card.textContent, /Remote/)
  assert.equal(card.querySelectorAll('.row-actions button').length, 3)
  assert.equal(container.querySelectorAll('.table-scroll tbody tr').length, 1)

  const detailsButton = card.querySelector('[aria-label^="View details"]')
  detailsButton.focus()
  await sessionClick(detailsButton)
  assert.equal(container.querySelector('.details-dialog').open, true)
  await sessionClick(container.querySelector('.details-dialog button'))
  assert.equal(document.activeElement, detailsButton)

  await sessionClick(card.querySelector('[aria-label^="Edit "]'))
  assert.equal(container.querySelector('.application-dialog h2').textContent, 'Edit application')
  await sessionClick(container.querySelector('.application-dialog .secondary-button'))
  assert.equal(container.querySelector('.application-dialog'), null)

  await sessionClick(card.querySelector('[aria-label^="Delete "]'))
  assert.equal(container.querySelector('.delete-dialog').open, true)
  assert.match(container.querySelector('.delete-dialog').textContent, /permanently removes/)
  await sessionClick(container.querySelector('.delete-dialog .secondary-button'))
  assert.equal(container.querySelector('.delete-dialog'), null)
  assert.equal(calls.filter(({ url }) => url === '/applications').length, 1)
})
