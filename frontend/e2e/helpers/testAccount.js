import { randomBytes } from 'node:crypto'

export function newTestAccount() {
  // example.com is reserved for examples and accepted by the backend's email
  // validator. The approved E2E stack captures mail locally, never externally.
  return {
    email: `playwright-${randomBytes(12).toString('hex')}@example.com`,
    password: randomBytes(32).toString('base64url'),
  }
}
