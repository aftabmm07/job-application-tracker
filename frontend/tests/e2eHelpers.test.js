import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { approvedE2EEnvironment } from '../e2e/helpers/approvedEnvironment.js'
import { waitForVerificationMessage } from '../e2e/helpers/mailpit.js'
import { newTestAccount } from '../e2e/helpers/testAccount.js'

const RUN_ID = 'a1b2c3d4e5f60718'
const ACCOUNT = 'playwright-0123456789abcdef01234567@example.com'
const TOKEN = 'SYNTHETIC_TOKEN_' + 'x'.repeat(43)

function approvedValues() {
  return {
    PLAYWRIGHT_E2E_RUN_ID: RUN_ID,
    PLAYWRIGHT_E2E_FRONTEND_PORT: '22080',
    PLAYWRIGHT_E2E_MAIL_UI_PORT: '22025',
    PLAYWRIGHT_BASE_URL: 'http://localhost:22080',
    PLAYWRIGHT_MAILPIT_URL: 'http://localhost:22025',
  }
}

function message(recipient = ACCOUNT) {
  return {
    ID: 'mail123', Subject: 'Verify your Job Application Tracker email',
    From: { Address: 'verify@jobtracker.test' }, To: [{ Address: recipient }],
    Snippet: TOKEN,
  }
}

test('accounts are unique, backend-compatible, and have no fixed password', () => {
  const first = newTestAccount()
  const second = newTestAccount()
  assert.match(first.email, /^playwright-[0-9a-f]{24}@example\.com$/)
  assert.notEqual(first.email, second.email)
  assert.notEqual(first.password, second.password)
  assert.ok(first.password.length >= 15 && first.password.length <= 128)
  assert.ok(!first.password.includes(first.email))
})

test('approved E2E target is exact and loopback-only', () => {
  assert.deepEqual(approvedE2EEnvironment(approvedValues()), {
    frontendOrigin: 'http://localhost:22080', mailpitOrigin: 'http://localhost:22025',
  })
  for (const change of [
    { PLAYWRIGHT_E2E_RUN_ID: '06479b7b1e0fc5d4' },
    { PLAYWRIGHT_E2E_RUN_ID: 'd3a273dcf94789a9' },
    { PLAYWRIGHT_E2E_RUN_ID: 'invalid' },
    { PLAYWRIGHT_BASE_URL: 'http://127.0.0.1:22080' },
    { PLAYWRIGHT_BASE_URL: `http://localhost:22080/verify-email#token=${TOKEN}` },
    { PLAYWRIGHT_BASE_URL: 'http://localhost:18080' },
    { PLAYWRIGHT_MAILPIT_URL: 'http://example.com:22025' },
    { PLAYWRIGHT_E2E_FRONTEND_PORT: '22025' },
    { PLAYWRIGHT_E2E_MAIL_UI_PORT: '22080' },
  ]) {
    assert.throws(() => approvedE2EEnvironment({ ...approvedValues(), ...change }),
      (error) => !String(error).includes(TOKEN))
  }
})

test('Mailpit search selects exact account and returns ID without reading body', async () => {
  let requested
  const result = await waitForVerificationMessage({
    mailpitOrigin: 'http://localhost:22025', recipient: ACCOUNT,
    environment: approvedValues(),
    fetchImpl: async (url, options) => {
      requested = { url, options }
      return { ok: true, json: async () => ({ messages: [
        message('playwright-ffffffffffffffffffffffff@example.com'), message(ACCOUNT),
      ] }) }
    },
  })
  assert.equal(result, 'mail123')
  assert.equal(requested.url.pathname, '/api/v1/search')
  assert.equal(requested.url.searchParams.get('query'), `to:${ACCOUNT}`)
  assert.equal(requested.options.method, 'GET')
  assert.equal(requested.options.redirect, 'error')
})

test('Mailpit lookup polls a bounded number of times for one account', async () => {
  let tick = 0
  let calls = 0
  const id = await waitForVerificationMessage({
    mailpitOrigin: 'http://localhost:22025', recipient: ACCOUNT,
    environment: approvedValues(), timeoutMs: 100, intervalMs: 25,
    now: () => tick, sleep: async (ms) => { tick += ms },
    fetchImpl: async () => {
      calls += 1
      return { ok: true, json: async () => ({ messages: calls === 1 ? [] : [message()] }) }
    },
  })
  assert.equal(id, 'mail123')
  assert.equal(calls, 2)
})

test('Mailpit recovers from transport failure and HTTP 5xx without leaking details', async () => {
  let tick = 0
  let calls = 0
  const requestedTimeouts = []
  const id = await waitForVerificationMessage({
    mailpitOrigin: 'http://localhost:22025', recipient: ACCOUNT,
    environment: approvedValues(), timeoutMs: 100, intervalMs: 25,
    now: () => tick, sleep: async (ms) => { tick += ms },
    requestSignal: (ms) => { requestedTimeouts.push(ms); return {} },
    fetchImpl: async () => {
      calls += 1
      if (calls === 1) throw new Error(TOKEN)
      if (calls === 2) return { status: 503, ok: false, text: async () => TOKEN }
      return { status: 200, ok: true, json: async () => ({ messages: [message()] }) }
    },
  })
  assert.equal(id, 'mail123')
  assert.equal(calls, 3)
  assert.deepEqual(requestedTimeouts, [100, 75, 50])
})

test('persistent transient Mailpit failures stop at the deadline with sanitized error', async () => {
  let tick = 0
  let calls = 0
  const requestedTimeouts = []
  await assert.rejects(waitForVerificationMessage({
    mailpitOrigin: 'http://localhost:22025', recipient: ACCOUNT,
    environment: approvedValues(), timeoutMs: 100, intervalMs: 40,
    now: () => tick, sleep: async (ms) => { tick += ms },
    requestSignal: (ms) => { requestedTimeouts.push(ms); return {} },
    fetchImpl: async () => {
      calls += 1
      if (calls % 2) throw new Error(TOKEN)
      return { status: 502, ok: false, json: async () => { throw new Error(TOKEN) } }
    },
  }), (error) => error.message === 'E2E mailbox unavailable within the E2E timeout'
    && !String(error).includes(TOKEN) && !String(error).includes(ACCOUNT))
  assert.equal(tick, 100)
  assert.equal(calls, 3)
  assert.deepEqual(requestedTimeouts, [100, 60, 20])
})

test('Mailpit enforces deadline after a slow request and caps request timeout', async () => {
  let tick = 0
  let calls = 0
  const requestedTimeouts = []
  await assert.rejects(waitForVerificationMessage({
    mailpitOrigin: 'http://localhost:22025', recipient: ACCOUNT,
    environment: approvedValues(), timeoutMs: 100, intervalMs: 25,
    now: () => tick, sleep: async (ms) => { tick += ms },
    requestSignal: (ms) => { requestedTimeouts.push(ms); return {} },
    fetchImpl: async () => {
      calls += 1
      tick = 100
      return { status: 200, ok: true, json: async () => ({ messages: [message()] }) }
    },
  }), /E2E timeout/)
  assert.equal(calls, 1)
  assert.deepEqual(requestedTimeouts, [100])

  tick = 0
  requestedTimeouts.length = 0
  await waitForVerificationMessage({
    mailpitOrigin: 'http://localhost:22025', recipient: ACCOUNT,
    environment: approvedValues(), timeoutMs: 3100, intervalMs: 250,
    now: () => tick, sleep: async (ms) => { tick += ms },
    requestSignal: (ms) => { requestedTimeouts.push(ms); return {} },
    fetchImpl: async () => requestedTimeouts.length === 1
      ? { status: 500, ok: false }
      : { status: 200, ok: true, json: async () => ({ messages: [message()] }) },
  })
  assert.deepEqual(requestedTimeouts, [3000, 2850])
})

test('Mailpit rejects ambiguous results and sanitizes transport errors', async () => {
  await assert.rejects(waitForVerificationMessage({
    mailpitOrigin: 'http://localhost:22025', recipient: ACCOUNT,
    environment: approvedValues(),
    fetchImpl: async () => ({ ok: true, json: async () => ({ messages: [message(), message()] }) }),
  }), (error) => !String(error).includes(TOKEN) && !String(error).includes(ACCOUNT))
  let tick = 0
  await assert.rejects(waitForVerificationMessage({
    mailpitOrigin: 'http://localhost:22025', recipient: ACCOUNT,
    environment: approvedValues(), timeoutMs: 2, intervalMs: 1,
    now: () => tick, sleep: async (ms) => { tick += ms },
    fetchImpl: async () => { throw new Error(TOKEN) },
  }), (error) => !String(error).includes(TOKEN))
  for (const response of [
    { status: 400, ok: false, text: async () => TOKEN },
    { status: 200, ok: true, json: async () => { throw new Error(TOKEN) } },
    { status: 200, ok: true, json: async () => ({ messages: 'invalid' }) },
  ]) {
    let calls = 0
    await assert.rejects(waitForVerificationMessage({
      mailpitOrigin: 'http://localhost:22025', recipient: ACCOUNT,
      environment: approvedValues(),
      fetchImpl: async () => { calls += 1; return response },
    }), (error) => error.message === 'E2E mailbox lookup failed'
      && !String(error).includes(TOKEN))
    assert.equal(calls, 1)
  }
})

test('Mailpit lookup rejects unapproved destination and recipient before fetch', async () => {
  let called = false
  await assert.rejects(waitForVerificationMessage({
    mailpitOrigin: 'http://example.com:22025', recipient: ACCOUNT,
    environment: approvedValues(),
    fetchImpl: async () => { called = true },
  }))
  await assert.rejects(waitForVerificationMessage({
    mailpitOrigin: 'http://localhost:22025', recipient: 'person@example.com',
    environment: approvedValues(),
    fetchImpl: async () => { called = true },
  }))
  assert.equal(called, false)
})

test('failure attachment allowlists routes and keeps Playwright rich artifacts off', () => {
  const fixture = readFileSync(new URL('../e2e/fixtures/safeTest.js', import.meta.url), 'utf8')
  const config = readFileSync(new URL('../playwright.config.js', import.meta.url), 'utf8')
  assert.match(fixture, /testInfo\.status === 'failed'/)
  assert.match(fixture, /ROUTES\.has\(pathname\)/)
  assert.match(config, /screenshot: 'off'/)
  assert.match(config, /video: 'off'/)
  assert.match(config, /trace: 'off'/)
  assert.doesNotMatch(config, /webServer:/)
  assert.match(readFileSync(new URL('../e2e/homepage.spec.js', import.meta.url), 'utf8'),
    /from '\.\/fixtures\/safeTest\.js'/)
})
