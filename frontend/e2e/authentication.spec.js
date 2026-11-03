import { test } from './fixtures/safeTest.js'
import { registerVerifyAndSignIn } from './helpers/authenticate.js'

test('register, verify email, and sign in through the browser', async ({ page }) => {
  await registerVerifyAndSignIn(page)
})
