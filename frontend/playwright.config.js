import { defineConfig } from '@playwright/test'
import { approvedE2EEnvironment } from './e2e/helpers/approvedEnvironment.js'

const { frontendOrigin: baseURL } = approvedE2EEnvironment()

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
