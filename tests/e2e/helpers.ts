import {browserJson, expect, type E2EServer, type Page} from './fixtures'

export type Wallet = {
  adminkey: string
  id: string
  inkey: string
  name: string
}

export async function login(page: Page, e2eServer: E2EServer): Promise<void> {
  await page.goto('/')
  await page.locator('input[name="username"]').fill(e2eServer.username)
  await page.locator('input[name="password"]').fill(e2eServer.password)
  await page.getByRole('button', {name: /^login$/i}).click()
  await expect(page).toHaveURL(/\/wallet\/[^/]+$/)
}

export async function superuserWallet(page: Page): Promise<Wallet> {
  return page.evaluate(() => {
    const wallet = (window as typeof window & {g: {user: {wallets: Wallet[]}}})
      .g.user.wallets[0]
    return {
      adminkey: wallet.adminkey,
      id: wallet.id,
      inkey: wallet.inkey,
      name: wallet.name
    }
  })
}

export async function createWallet(page: Page, name: string): Promise<Wallet> {
  return (await browserJson(page, 'POST', '/api/v1/wallet', {name})) as Wallet
}

export async function createTpos(
  page: Page,
  wallet: Wallet,
  data: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const tposData = {...data}
  const items = tposData.items
  delete tposData.items
  const tpos = (await browserJson(
    page,
    'POST',
    '/tpos/api/v1/tposs',
    tposData,
    wallet.adminkey
  )) as Record<string, unknown>
  expect(tpos.id).toEqual(expect.any(String))
  if (items) {
    const parsedItems =
      typeof items === 'string' ? (JSON.parse(items) as unknown[]) : items
    await browserJson(
      page,
      'PUT',
      `/tpos/api/v1/tposs/${tpos.id}/items`,
      {items: parsedItems},
      wallet.adminkey
    )
  }
  return tpos
}

export async function payInvoice(
  page: Page,
  wallet: Wallet,
  paymentRequest: string
): Promise<void> {
  await browserJson(
    page,
    'POST',
    '/api/v1/payments',
    {out: true, bolt11: paymentRequest},
    wallet.adminkey
  )
}

export async function fundWallet(
  page: Page,
  wallet: Wallet,
  amountSats: number
): Promise<void> {
  const result = await browserJson(page, 'PUT', '/users/api/v1/balance', {
    id: wallet.id,
    amount: amountSats
  })
  expect(result).toMatchObject({success: true})
}
