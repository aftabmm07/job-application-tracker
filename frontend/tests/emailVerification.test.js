import assert from 'node:assert/strict'
import test from 'node:test'
import { createVerificationAttempt } from '../src/auth/emailVerification.js'
import { verifyEmail } from '../src/auth/authClient.js'

const token = 'a'.repeat(43)
const csrf = 'b'.repeat(64)

function browser(hash = `#token=${token}`, search = '') {
  const state = { key: 'router-key', idx: 2, usr: null }
  const instance = {
    location: { pathname: '/verify-email', hash, search },
    history: {
      state,
      replaceState(nextState, _, url) {
        assert.equal(nextState, state)
        assert.equal(url, '/verify-email')
        instance.location.hash = ''
        instance.location.search = ''
      },
    },
  }
  return instance
}

function response(status, body = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

test('clears the fragment synchronously before any async request; memoizes replay', async () => {
  const window = browser()
  let calls = 0
  const start = createVerificationAttempt(window, async (submitted) => {
    calls++
    assert.equal(window.location.hash, '')
    assert.equal(submitted, token)
    return 'success'
  })
  const first = start()
  assert.equal(window.location.hash, '')
  assert.equal(calls, 0)
  assert.equal(start(), first) // StrictMode effect setup/cleanup/setup uses one attempt.
  assert.equal(await first, 'success')
  assert.equal(await start(), 'success') // Rerenders never replay a consumed token.
  assert.equal(calls, 1)
})

for (const hash of ['', '#', '#token=', '#token=bad', `#token=${token}&token=${token}`,
  `#other=${token}`, `#token=${token}&extra=1`, `#token=${'a'.repeat(44)}`,
  `#token=${'é'.repeat(43)}`, `#token=%61${'a'.repeat(42)}`, `#token=${token}\n`]) {
  test(`rejects malformed fragment case ${JSON.stringify(hash.slice(0, 12))}`, async () => {
    const window = browser(hash)
    const start = createVerificationAttempt(window, () => assert.fail('Must not submit'))
    assert.equal(await start(), 'invalid')
    assert.equal(window.location.hash, '')
  })
}

test('does not accept query-string tokens and removes unsupported query parameters', async () => {
  const window = browser('', `?token=${token}`)
  const start = createVerificationAttempt(window, () => assert.fail('Must not submit'))
  assert.equal(await start(), 'invalid')
  assert.equal(window.location.search, '')
})

test('fails closed if history replacement fails', async () => {
  const window = browser()
  window.history.replaceState = () => { throw new Error(token) }
  const start = createVerificationAttempt(window, () => assert.fail('Must not submit'))
  assert.equal(await start(), 'temporary-error')
  assert.equal(await start(), 'temporary-error')
})

test('unexpected failures become fixed states, never exception contents', async () => {
  const start = createVerificationAttempt(browser(), () => { throw new Error(token) })
  assert.equal(await start(), 'temporary-error')
})

test('bootstraps CSRF then submits JSON with credentials; never places token in URL', async (t) => {
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push([url, options])
    return url === '/auth/csrf' ? response(200, { csrf_token: csrf }) : response(200)
  })
  assert.equal(await verifyEmail(token), 'success')
  assert.deepEqual(calls.map(([url]) => url), ['/auth/csrf', '/auth/verify-email'])
  for (const [url, options] of calls) {
    assert.equal(url.includes(token), false)
    assert.equal(options.credentials, 'same-origin')
    assert.equal(options.cache, 'no-store')
  }
  const post = calls[1][1]
  assert.equal(post.method, 'POST')
  assert.deepEqual(post.headers, { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf })
  assert.deepEqual(JSON.parse(post.body), { token })
})

for (const [status, expected] of [[400, 'invalid'], [422, 'invalid'], [403, 'temporary-error'],
  [429, 'temporary-error'], [503, 'temporary-error'], [500, 'temporary-error']]) {
  test(`maps HTTP ${status} to a safe ${expected} state`, async (t) => {
    t.mock.method(globalThis, 'fetch', async (url) => url === '/auth/csrf'
      ? response(200, { csrf_token: csrf }) : response(status, { detail: token }))
    assert.equal(await verifyEmail(token), expected)
  })
}

test('CSRF bootstrap failure prevents submission and stays a temporary error', async (t) => {
  let calls = 0
  t.mock.method(globalThis, 'fetch', async () => { calls++; return response(403, { detail: token }) })
  assert.equal(await verifyEmail(token), 'temporary-error')
  assert.equal(calls, 1)
})

test('invalid CSRF response prevents submission', async (t) => {
  let calls = 0
  t.mock.method(globalThis, 'fetch', async () => { calls++; return response(200, { csrf_token: token }) })
  assert.equal(await verifyEmail(token), 'temporary-error')
  assert.equal(calls, 1)
})

test('network failure after bootstrap is a temporary error without automatic retry', async (t) => {
  let calls = 0
  t.mock.method(globalThis, 'fetch', async () => {
    calls++
    if (calls === 1) return response(200, { csrf_token: csrf })
    throw new Error(token)
  })
  assert.equal(await verifyEmail(token), 'temporary-error')
  assert.equal(calls, 2)
})
