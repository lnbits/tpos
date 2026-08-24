import {defineConfig} from '@playwright/test'
import {resolve} from 'node:path'

const extensionRoot = resolve(__dirname, '../..')
const lnbitsRoot = resolve(extensionRoot, '../../..')
const baseURL = process.env.LNBITS_E2E_BASE_URL ?? 'http://127.0.0.1:5019'
const reportRoot = resolve(extensionRoot, 'test-reports')

export default defineConfig({
  testDir: __dirname,
  testMatch: '**/*.spec.ts',
  outputDir: resolve(reportRoot, 'playwright-results'),
  timeout: 600_000,
  fullyParallel: false,
  workers: 1,
  expect: {timeout: 15_000},
  reporter: [
    ['list'],
    [
      'html',
      {open: 'never', outputFolder: resolve(reportRoot, 'playwright-report')}
    ]
  ],
  use: {
    baseURL,
    browserName: 'chromium',
    headless: true,
    viewport: {width: 1280, height: 900},
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    launchOptions: {
      ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH
        ? {executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH}
        : {})
    }
  },
  webServer: {
    command: 'node ./lnbits/extensions/tpos/tests/e2e/start-tpos-server.cjs',
    cwd: lnbitsRoot,
    url: baseURL,
    reuseExistingServer: Boolean(process.env.LNBITS_E2E_REUSE_SERVER),
    timeout: 180_000
  }
})
