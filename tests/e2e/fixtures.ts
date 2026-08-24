import {randomUUID} from 'node:crypto'

import {test as base, expect, type Browser, type Page} from '@playwright/test'

export type E2EServer = {baseUrl: string; username: string; password: string}
export const server: E2EServer = {
  baseUrl: process.env.LNBITS_E2E_BASE_URL ?? 'http://127.0.0.1:5019',
  username: 'superadmin',
  password: 'secret1234'
}

export const test = base.extend<{
  lnbitsServer: E2EServer
  tposEnabled: boolean
}>({
  lnbitsServer: [
    async ({}, use) => {
      await completeFirstInstall(server)
      await use(server)
    },
    {scope: 'worker'}
  ],
  tposEnabled: [
    async ({browser, lnbitsServer}, use) => {
      await enableTposForAccount(browser, lnbitsServer)
      await use(true)
    },
    {scope: 'worker'}
  ],
  page: async ({page, tposEnabled}, use) => {
    void tposEnabled
    await page.addInitScript(
      "window.localStorage.setItem('lnbits.disclaimerShown', 'true')"
    )
    page.setDefaultTimeout(60_000)
    const pageErrors: Error[] = []
    page.on('pageerror', error => pageErrors.push(error))
    await use(page)
    if (pageErrors.length) {
      throw new Error(
        `Uncaught browser error(s): ${pageErrors.map(error => error.message).join('; ')}`
      )
    }
  }
})

export {expect}

export function randomHex(): string {
  return randomUUID().replaceAll('-', '').slice(0, 12)
}

export async function browserJson(
  page: Page,
  method: string,
  path: string,
  data?: Record<string, unknown>,
  apiKey?: string
): Promise<unknown> {
  return page.evaluate(
    async ({method, path, data, apiKey}) => {
      const response = await fetch(path, {
        method,
        headers: {
          'Content-Type': 'application/json',
          ...(apiKey ? {'X-Api-Key': apiKey} : {})
        },
        credentials: 'same-origin',
        body: data === undefined ? undefined : JSON.stringify(data)
      })
      const text = await response.text()
      let body: unknown = {}
      try {
        body = text ? JSON.parse(text) : {}
      } catch (_error) {
        body = {detail: text}
      }
      if (!response.ok) {
        throw new Error(`${method} ${path} failed: ${response.status} ${text}`)
      }
      return body
    },
    {method, path, data, apiKey}
  )
}

export async function waitForResult<T>(
  description: string,
  callback: () => Promise<T | null>,
  timeout = 30_000
): Promise<T> {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const result = await callback().catch(() => null)
    if (result !== null) return result
    await new Promise(resolve => setTimeout(resolve, 500))
  }
  throw new Error(`Timed out waiting for ${description}`)
}

async function completeFirstInstall(e2eServer: E2EServer): Promise<void> {
  const deadline = Date.now() + 90_000
  while (Date.now() < deadline) {
    let response: Response | undefined
    try {
      response = await fetch(`${e2eServer.baseUrl}/api/v1/auth/first_install`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          username: e2eServer.username,
          password: e2eServer.password,
          password_repeat: e2eServer.password,
          first_install_token: ''
        })
      })
    } catch (_error) {}
    if (response?.status === 200) return
    if (response?.status === 403) {
      const body = (await response.json()) as {detail?: unknown}
      if (body.detail === 'This is not your first install') return
      throw new Error(
        `Unexpected first-install response: ${JSON.stringify(body)}`
      )
    }
    await new Promise(resolve => setTimeout(resolve, 500))
  }
  throw new Error('LNbits E2E server did not complete first install')
}

async function enableTposForAccount(
  browser: Browser,
  e2eServer: E2EServer
): Promise<void> {
  const context = await browser.newContext({baseURL: e2eServer.baseUrl})
  const page = await context.newPage()
  try {
    await page.goto('/')
    await page.locator('input[name="username"]').fill(e2eServer.username)
    await page.locator('input[name="password"]').fill(e2eServer.password)
    await page.getByRole('button', {name: /^login$/i}).click()
    await expect(page).toHaveURL(/\/wallet\/[^/]+$/)
    const response = await page.evaluate(async () => {
      const result = await fetch('/api/v1/extension/tpos/enable', {
        method: 'PUT',
        credentials: 'same-origin'
      })
      return {body: await result.text(), status: result.status}
    })
    if (response.status !== 200) {
      throw new Error(
        `Could not enable local TPoS extension: ${response.status} ${response.body}`
      )
    }
  } finally {
    await context.close()
  }
}
