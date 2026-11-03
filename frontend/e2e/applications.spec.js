import { randomBytes } from 'node:crypto'
import { test, expect } from './fixtures/safeTest.js'
import { approvedE2EEnvironment } from './helpers/approvedEnvironment.js'
import { registerVerifyAndSignIn } from './helpers/authenticate.js'

test('create, read, update, and delete one application through the UI', async ({ page }) => {
  approvedE2EEnvironment()
  await registerVerifyAndSignIn(page)

  const suffix = randomBytes(8).toString('hex')
  const company = `Example Studio ${suffix}`
  const title = `Portfolio Engineer ${suffix}`
  const updatedTitle = `Senior Portfolio Engineer ${suffix}`
  const description = `Fictional position for E2E run ${suffix}.`
  const table = page.getByRole('region', { name: 'Job applications table' })
  const rowFor = (jobTitle) => table.getByRole('row').filter({
    has: page.getByRole('rowheader', { name: company, exact: true }),
  }).filter({ has: page.getByRole('cell', { name: jobTitle, exact: true }) })

  await page.getByRole('button', { name: '+ Add application' }).click()
  const addDialog = page.getByRole('dialog', { name: 'Add new application' })
  await expect(addDialog).toBeVisible()
  await addDialog.getByRole('textbox', { name: 'Company name *' }).fill(company)
  await addDialog.getByRole('textbox', { name: 'Job title *' }).fill(title)
  await addDialog.getByRole('textbox', { name: 'Location' }).fill('Fictional City')
  await addDialog.getByRole('textbox', { name: 'Job description' }).fill(description)
  await addDialog.getByRole('button', { name: 'Save application' }).click()
  await expect(page.getByRole('status').getByText('Application added successfully.')).toBeVisible()
  await expect(rowFor(title)).toHaveCount(1)
  await expect(rowFor(title)).toContainText('saved')

  await rowFor(title).getByRole('button', { name: `View details for ${title} at ${company}` }).click()
  const details = page.getByRole('dialog', { name: 'Application details' })
  await expect(details).toBeVisible()
  await expect(details).toContainText(company)
  await expect(details).toContainText(title)
  await expect(details).toContainText('Fictional City')
  await expect(details).toContainText(description)
  await details.getByRole('button', { name: 'Close details' }).click()
  await expect(details).toHaveCount(0)

  await rowFor(title).getByRole('button', { name: `Edit ${title} at ${company}` }).click()
  const editDialog = page.getByRole('dialog', { name: 'Edit application' })
  await expect(editDialog).toBeVisible()
  await editDialog.getByRole('textbox', { name: 'Job title *' }).fill(updatedTitle)
  await editDialog.getByRole('combobox', { name: 'Status' }).selectOption('applied')
  await editDialog.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByRole('status').getByText('Application updated successfully.')).toBeVisible()
  await expect(rowFor(title)).toHaveCount(0)
  await expect(rowFor(updatedTitle)).toHaveCount(1)
  await expect(rowFor(updatedTitle)).toContainText('applied')

  await rowFor(updatedTitle).getByRole('button', { name: `View details for ${updatedTitle} at ${company}` }).click()
  await expect(details).toBeVisible()
  await expect(details.getByText(updatedTitle, { exact: true })).toBeVisible()
  await expect(details.getByText('applied', { exact: true })).toBeVisible()
  await details.getByRole('button', { name: 'Close details' }).click()
  await expect(details).toHaveCount(0)

  await rowFor(updatedTitle).getByRole('button', { name: `Delete ${updatedTitle} at ${company}` }).click()
  const deleteDialog = page.getByRole('dialog', { name: 'Delete application?' })
  await expect(deleteDialog).toBeVisible()
  await expect(deleteDialog).toContainText(updatedTitle)
  await expect(deleteDialog).toContainText(company)
  await deleteDialog.getByRole('button', { name: 'Delete Application' }).click()
  await expect(page.getByRole('status').getByText('Application deleted successfully.')).toBeVisible()
  await expect(rowFor(updatedTitle)).toHaveCount(0)
})
