import { defineConfig } from '@playwright/test'

const rawBaseURL = process.env.PLAYWRIGHT_BASE_URL
let baseURL
try {
  const parsed = new URL(rawBaseURL)
  if (parsed.protocol !== 'http:' || !['localhost', '127.0.0.1'].includes(parsed.hostname)
      || parsed.username || parsed.password || parsed.pathname !== '/'
      || parsed.search || parsed.hash || !parsed.port) {
    throw new Error()
  }
  baseURL = parsed.origin
} catch {
  // Never echo a supplied URL: it could contain credentials or a bearer token.
  throw new Error('PLAYWRIGHT_BASE_URL must be an explicit local HTTP origin with a port')
}

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL,
    browserName: 'chromium',
    headless: true,
    screenshot: 'off',
    video: 'off',
    trace: 'off',
  },
})
