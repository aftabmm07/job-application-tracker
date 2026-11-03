const RESERVED_RUN_IDS = new Set(['06479b7b1e0fc5d4', 'd3a273dcf94789a9'])

function approvedPort(value) {
  return typeof value === 'string' && /^[1-9][0-9]{4}$/.test(value)
    && Number(value) >= 20000 && Number(value) <= 60999
}

export function approvedE2EEnvironment(env = process.env) {
  const runId = env.PLAYWRIGHT_E2E_RUN_ID
  const frontendPort = env.PLAYWRIGHT_E2E_FRONTEND_PORT
  const mailpitPort = env.PLAYWRIGHT_E2E_MAIL_UI_PORT
  if (typeof runId !== 'string' || !/^[0-9a-f]{16}$/.test(runId)
      || RESERVED_RUN_IDS.has(runId) || !approvedPort(frontendPort)
      || !approvedPort(mailpitPort) || frontendPort === mailpitPort
      || env.PLAYWRIGHT_BASE_URL !== `http://localhost:${frontendPort}`
      || env.PLAYWRIGHT_MAILPIT_URL !== `http://localhost:${mailpitPort}`) {
    // Do not echo supplied values: they could contain a token or credentials.
    throw new Error('Explicit approved local E2E origins and run identity are required')
  }
  return {
    frontendOrigin: `http://localhost:${frontendPort}`,
    mailpitOrigin: `http://localhost:${mailpitPort}`,
  }
}
