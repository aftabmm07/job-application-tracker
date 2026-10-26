// Keep tokens out of React state, persistent storage, and messages.
export function createVerificationAttempt(browser, verifyEmail) {
  let result

  return function start() {
    // Reuse the same promise during StrictMode effect replay and rerenders.
    if (result) return result

    let fragment
    try {
      fragment = browser.location.hash
      // Preserve React Router's history metadata, but remove the fragment and
      // unsupported query parameters before parsing or starting any requests.
      browser.history.replaceState(browser.history.state, '', browser.location.pathname)
    } catch {
      // Fail closed if history cannot be cleaned. Never send the bearer.
      result = Promise.resolve('temporary-error')
      return result
    }

    const match = /^#token=([A-Za-z0-9_-]{43})$/.exec(fragment)
    if (!match) {
      result = Promise.resolve('invalid')
      return result
    }

    result = Promise.resolve()
      .then(() => verifyEmail(match[1]))
      .catch(() => 'temporary-error')
    return result
  }
}
