import { test as base, expect } from '@playwright/test'

const ROUTES = new Set(['/','/login','/register','/verify-email','/applications','/analytics','/profile'])

// Future sensitive flows should import this fixture. Failure attachments contain
// only a known route label; no screenshot, trace, URL fragment, body, or cookie.
export const test = base.extend({
  page: async ({ page }, providePage, testInfo) => {
    await providePage(page)
    if (testInfo.status === 'failed') {
      let route = 'other'
      try {
        const pathname = new URL(page.url()).pathname
        if (ROUTES.has(pathname)) route = pathname
      } catch {
        // An unavailable page URL is reported only as "other".
      }
      await testInfo.attach('safe-route', {
        body: Buffer.from(`route=${route}\n`), contentType: 'text/plain',
      })
    }
  },
})

export { expect }
