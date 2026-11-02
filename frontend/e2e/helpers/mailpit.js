import { approvedE2EEnvironment } from './approvedEnvironment.js'

const SUBJECT = 'Verify your Job Application Tracker email'
const FROM = 'verify@jobtracker.test'

export async function waitForVerificationMessage({ mailpitOrigin, recipient, environment = process.env,
  fetchImpl = fetch,
  timeoutMs = 10000, intervalMs = 250, now = Date.now,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  requestSignal = (ms) => AbortSignal.timeout(ms) }) {
  // A unique account address selects one message without mailbox deletion or
  // relying on the globally latest message. Do not fetch message body here.
  const approvedOrigin = approvedE2EEnvironment(environment).mailpitOrigin
  if (!/^playwright-[0-9a-f]{24}@example\.com$/.test(recipient)
      || mailpitOrigin !== approvedOrigin
      || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30000
      || !Number.isSafeInteger(intervalMs) || intervalMs < 1 || intervalMs > 1000) {
    throw new Error('Invalid E2E mailbox request')
  }
  const deadline = now() + timeoutMs
  const endpoint = new URL('/api/v1/search', mailpitOrigin)
  endpoint.searchParams.set('query', `to:${recipient}`)
  endpoint.searchParams.set('limit', '50')

  let sawTransientFailure = false
  while (now() < deadline) {
    const remaining = deadline - now()
    if (remaining <= 0) break
    let response
    try {
      response = await fetchImpl(endpoint, {
        method: 'GET', redirect: 'error', headers: { Accept: 'application/json' },
        signal: requestSignal(Math.max(1, Math.min(3000, Math.ceil(remaining)))),
      })
    } catch {
      // Transport errors, including an aborted request, may be temporary.
      // Never include the underlying exception, URL, or mailbox data.
      sawTransientFailure = true
    }
    if (now() >= deadline) break
    if (response?.status >= 500 && response.status <= 599) {
      sawTransientFailure = true
    } else if (response) {
      if (!response.ok) throw new Error('E2E mailbox lookup failed')
      try {
        const result = await response.json()
        if (!Array.isArray(result?.messages) || result.messages_count > 50) throw new Error()
        const matches = result.messages.filter((message) =>
          message?.Subject === SUBJECT && message?.From?.Address === FROM
          && Array.isArray(message?.To) && message.To.length === 1
          && message.To[0]?.Address === recipient
          && typeof message.ID === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(message.ID))
        if (matches.length > 1) throw new Error()
        if (now() >= deadline) break
        if (matches.length === 1) return matches[0].ID
      } catch {
        // Malformed or ambiguous responses are hard failures, not retryable.
        throw new Error('E2E mailbox lookup failed')
      }
    }
    const wait = Math.min(intervalMs, Math.max(0, deadline - now()))
    if (wait > 0) {
      try { await sleep(wait) } catch { throw new Error('E2E mailbox lookup failed') }
    }
  }
  if (sawTransientFailure) throw new Error('E2E mailbox unavailable within the E2E timeout')
  throw new Error('Verification email did not arrive within the E2E timeout')
}

export async function readVerificationToken({ mailpitOrigin, messageId, recipient,
  environment = process.env, fetchImpl = fetch }) {
  const { frontendOrigin, mailpitOrigin: approvedMailpitOrigin } =
    approvedE2EEnvironment(environment)
  if (mailpitOrigin !== approvedMailpitOrigin
      || !/^playwright-[0-9a-f]{24}@example\.com$/.test(recipient)
      || typeof messageId !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(messageId)) {
    throw new Error('Invalid E2E mailbox request')
  }
  try {
    const endpoint = new URL(`/api/v1/message/${messageId}`, mailpitOrigin)
    const response = await fetchImpl(endpoint, {
      method: 'GET', redirect: 'error', headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(3000),
    })
    if (!response.ok) throw new Error()
    const message = await response.json()
    if (message?.ID !== messageId || message.Subject !== SUBJECT
        || message.From?.Address !== FROM || !Array.isArray(message.To)
        || message.To.length !== 1 || message.To[0]?.Address !== recipient
        || typeof message.Text !== 'string' || message.Text.length > 8192) {
      throw new Error()
    }
    const links = message.Text.match(/https?:\/\/[^\s<>"']+/g)
    if (!links || links.length !== 1) throw new Error()
    const link = new URL(links[0])
    const match = /^#token=([A-Za-z0-9_-]{43})$/.exec(link.hash)
    if (!match || link.origin !== frontendOrigin || link.pathname !== '/verify-email'
        || link.search || link.username || link.password
        || links[0] !== `${frontendOrigin}/verify-email#token=${match[1]}`) {
      throw new Error()
    }
    // Return only the transient bearer. Never return or persist the body/link.
    return match[1]
  } catch {
    // The API body, URL, and transport error can contain bearer material.
    throw new Error('E2E verification email could not be validated')
  }
}
