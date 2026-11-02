import { test, expect } from './fixtures/safeTest.js'
import { approvedE2EEnvironment } from './helpers/approvedEnvironment.js'
import { newTestAccount } from './helpers/testAccount.js'
import { readVerificationToken, waitForVerificationMessage } from './helpers/mailpit.js'

test('register, verify email, and sign in through the browser', async ({ page }) => {
  const { frontendOrigin, mailpitOrigin } = approvedE2EEnvironment()
  const account = newTestAccount()

  await page.goto('/register')
  await expect(page.getByRole('heading', { name: 'Create your account' })).toBeVisible()
  try {
    await page.getByLabel('Email').fill(account.email)
    await page.getByLabel('Password').fill(account.password)
  } catch {
    throw new Error('E2E registration form could not be filled')
  }
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByText('Registration received. Account activation is required before sign-in.')).toBeVisible()

  const messageId = await waitForVerificationMessage({ mailpitOrigin, recipient: account.email })
  const token = await readVerificationToken({ mailpitOrigin, messageId, recipient: account.email })
  // Passing only an opaque argument avoids a bearer URL in Playwright's
  // navigation command and its failure output. The browser fragment is never
  // sent in HTTP; VerifyEmailPage clears it before asynchronous requests.
  try {
    await page.evaluate((bearer) => {
      window.location.assign(`/verify-email#token=${bearer}`)
    }, token)
  } catch {
    throw new Error('E2E verification navigation failed')
  }
  await expect(page.getByText('Your email is verified. You can now sign in.')).toBeVisible()
  expect(await page.evaluate(() => window.location.hash === '')).toBe(true)

  await page.getByRole('link', { name: 'Go to sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
  try {
    await page.getByLabel('Email').fill(account.email)
    await page.getByLabel('Password').fill(account.password)
  } catch {
    throw new Error('E2E sign-in form could not be filled')
  }
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Applications', level: 1 })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible()
  expect(await page.evaluate((origin) => window.location.origin === origin, frontendOrigin)).toBe(true)
})
