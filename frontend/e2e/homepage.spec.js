import { expect, test } from '@playwright/test'

test('frontend homepage serves and mounts the application', async ({ page }) => {
  const response = await page.goto('/')

  expect(response?.status()).toBe(200)
  await expect(page).toHaveTitle('Job Application Tracker')
  await expect(page.locator('#root')).not.toBeEmpty()
})
