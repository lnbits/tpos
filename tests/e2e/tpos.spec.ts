import {test, expect, randomHex} from './fixtures'
import {
  createTpos,
  createWallet,
  fundWallet,
  login,
  payInvoice,
  superuserWallet
} from './helpers'

test('admin can create a terminal and add an item through the extracted dialogs', async ({
  page,
  lnbitsServer
}) => {
  await login(page, lnbitsServer)
  const wallet = await superuserWallet(page)
  const terminalName = `Admin terminal ${randomHex()}`
  const itemName = `Admin coffee ${randomHex()}`

  await page.goto('/tpos/')
  await page.getByRole('button', {name: 'New TPoS'}).click()
  const form = page.locator('.q-dialog').filter({hasText: 'Name *'}).last()
  await form.getByLabel('Name *').fill(terminalName)
  await form.getByLabel('Wallet *').click()
  await page.getByRole('option').filter({hasText: wallet.name}).click()
  await form.getByLabel('Currency *').click()
  await page.getByRole('option', {name: 'sats', exact: true}).click()
  await form.getByRole('button', {name: 'Create TPoS'}).click()

  const row = page.locator('tr').filter({hasText: terminalName}).first()
  await expect(row).toBeVisible()
  await row.locator('button').first().click()
  await page.getByRole('button', {name: 'Add Item'}).click()
  const itemDialog = page
    .locator('.q-dialog')
    .filter({hasText: 'Title *'})
    .last()
  await itemDialog.getByLabel('Title *').fill(itemName)
  await itemDialog.getByLabel(/Price \(sats\)/).fill('13')
  await itemDialog.getByRole('button', {name: 'Create Item'}).click()
  await expect(page.getByText(itemName, {exact: true})).toBeVisible()
})

test('public item checkout completes through Lightning with FakeWallet', async ({
  page,
  lnbitsServer
}) => {
  await login(page, lnbitsServer)
  const merchantWallet = await superuserWallet(page)
  const customerWallet = await createWallet(
    page,
    `TPoS customer ${randomHex()}`
  )
  await fundWallet(page, customerWallet, 100)
  const itemName = `Playwright coffee ${randomHex()}`
  const terminal = await createTpos(page, merchantWallet, {
    name: `Playwright terminal ${randomHex()}`,
    currency: 'sats',
    wallet: merchantWallet.id,
    items: JSON.stringify([
      {
        title: itemName,
        description: 'Browser product',
        price: 21,
        tax: 0,
        disabled: false
      }
    ]),
    tip_options: '[]',
    enable_remote: false,
    tabs_enabled: false
  })

  await page.goto(`/tpos/${terminal.id}`)
  const pos = page.locator('body')
  const item = pos
    .locator('.item-grid-title:visible')
    .filter({hasText: itemName})
  await expect(item).toBeVisible()
  await item.click()
  await expect(pos.getByText('Total', {exact: true}).last()).toBeVisible()
  const invoiceResponse = page.waitForResponse(
    response =>
      response.request().method() === 'POST' &&
      response.url().includes(`/tpos/api/v1/tposs/${terminal.id}/invoices`)
  )
  await pos.getByRole('button', {name: /^pay$/i}).click()
  const invoice = (await (await invoiceResponse).json()) as {
    bolt11?: string
  }
  expect(invoice.bolt11).toMatch(/^lnbc/i)
  await payInvoice(page, customerWallet, invoice.bolt11 as string)
  await expect(pos.getByText('Invoice Paid!', {exact: true})).toBeVisible({
    timeout: 60_000
  })
  await expect(
    pos.locator('table tbody tr').filter({hasText: itemName})
  ).toHaveCount(0)
})

test('held carts survive restore and can then be deleted', async ({
  page,
  lnbitsServer
}) => {
  await login(page, lnbitsServer)
  const merchantWallet = await superuserWallet(page)
  const itemName = `Held coffee ${randomHex()}`
  const terminal = await createTpos(page, merchantWallet, {
    name: `Held cart terminal ${randomHex()}`,
    currency: 'sats',
    wallet: merchantWallet.id,
    items: JSON.stringify([
      {
        title: itemName,
        price: 7,
        tax: 0,
        disabled: false
      }
    ]),
    tip_options: '[]',
    enable_remote: false,
    tabs_enabled: false
  })

  await page.goto(`/tpos/${terminal.id}`)
  const pos = page.locator('body')
  await pos
    .locator('.item-grid-title:visible')
    .filter({hasText: itemName})
    .click()
  await pos.getByRole('button', {name: /hold cart/i}).click()
  const holdDialog = pos
    .locator('.q-dialog')
    .filter({hasText: /hold cart/i})
    .last()
  await expect(holdDialog).toBeVisible()
  await holdDialog.locator('input').fill('Lunch order')
  await holdDialog.getByRole('button', {name: /^ok$/i}).click()
  await expect(pos.getByText(/cart held successfully/i)).toBeVisible()

  await page.reload()
  await pos
    .locator('.q-fab:visible')
    .first()
    .getByRole('button')
    .first()
    .click()
  await pos.getByRole('button', {name: /carts on hold/i}).click()
  const heldDialog = pos
    .locator('.q-dialog')
    .filter({hasText: 'Lunch order'})
    .last()
  await expect(heldDialog).toBeVisible()
  await heldDialog.getByText('Lunch order', {exact: true}).click()
  await expect(
    pos.getByText(/cart restored successfully.*lunch order/i)
  ).toBeVisible()
  const restoreConfirmation = pos
    .locator('.q-dialog')
    .filter({hasText: /delete cart from held carts/i})
    .last()
  await restoreConfirmation.getByRole('button', {name: /cancel/i}).click()

  await page.reload()
  await pos
    .locator('.q-fab:visible')
    .first()
    .getByRole('button')
    .first()
    .click()
  await pos.getByRole('button', {name: /carts on hold/i}).click()
  const persistedDialog = pos
    .locator('.q-dialog')
    .filter({hasText: 'Lunch order'})
    .last()
  const persisted = persistedDialog
    .locator('.q-item')
    .filter({hasText: 'Lunch order'})
    .last()
  await expect(persisted).toBeVisible()
  await persisted.getByRole('button').click()
  await expect(pos.getByText(/cart deleted successfully/i)).toBeVisible()
  const heldCarts = await page.evaluate(() =>
    window.localStorage.getItem('lnbits.heldCarts')
  )
  expect(heldCarts).toBe('{}')
})
