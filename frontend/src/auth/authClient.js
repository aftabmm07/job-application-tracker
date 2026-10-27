
const AUTH_BASE = '/auth'

async function authRequest(path, options = {}) {
  const response = await fetch(`${AUTH_BASE}${path}`, {
    credentials: 'same-origin',
    cache: 'no-store',
    ...options,
  })

  if (!response.ok) {
    let message = `Authentication request failed (HTTP ${response.status}).`

    try {
      const data = await response.json()
      if (typeof data.detail === 'string') {
        message = data.detail
      }
    } catch {
      // Do not expose unexpected response bodies.
    }

    const error = new Error(message)
    error.status = response.status
    throw error
  }

  return response
}

export async function getCsrfToken({ signal } = {}) {
  const response = await authRequest('/csrf', { signal })
  const data = await response.json()

  if (typeof data.csrf_token !== 'string' || !/^[0-9a-f]{64}$/.test(data.csrf_token)) {
    throw new Error('Invalid CSRF response.')
  }

  return data.csrf_token
}

export async function getCurrentUser() {
  const response = await authRequest('/me')
  return response.json()
}

export async function getProfile({ signal } = {}) {
  const response = await authRequest('/profile', { signal })
  return response.json()
}

export async function updateProfile(changes, { signal } = {}) {
  const csrfToken = await getCsrfToken({ signal })
  const response = await authRequest('/profile', {
    method: 'PATCH',
    signal,
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
    },
    body: JSON.stringify(changes),
  })
  return response.json()
}

export async function login(email, password) {
  const csrfToken = await getCsrfToken()

  const response = await authRequest('/login', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
    },
    body: JSON.stringify({ email, password }),
  })

  const user = await response.json()
  // Login rotates the session. A failed refresh does not undo a successful
  // login, and the pre-login token must never be used for later mutations.
  try {
    await getCsrfToken()
    return { user, csrfReady: true }
  } catch {
    return { user, csrfReady: false }
  }
}


export async function register(email, password) {
  const csrfToken = await getCsrfToken()

  const response = await authRequest('/register', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
    },
    body: JSON.stringify({ email, password }),
  })

  return response.json()
}

export async function resendVerification(email) {
  const csrfToken = await getCsrfToken()
  await authRequest('/resend-verification', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
    },
    body: JSON.stringify({ email }),
  })
}


export async function logout() {
  const csrfToken = await getCsrfToken()

  await authRequest('/logout', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
    },
    body: JSON.stringify({}),
  })
}


export async function verifyEmail(token) {
  let csrfToken
  try {
    csrfToken = await getCsrfToken()
  } catch {
    return 'temporary-error'
  }

  try {
    await authRequest('/verify-email', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken,
      },
      body: JSON.stringify({ token }),
    })
    return 'success'
  } catch (error) {
    // Never display server messages or exception contents for bearer tokens.
    return error.status === 400 || error.status === 422 ? 'invalid' : 'temporary-error'
  }
}
