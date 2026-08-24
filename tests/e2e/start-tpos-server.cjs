const childProcess = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const extensionRoot = path.resolve(__dirname, '../..')
const lnbitsRoot = path.resolve(extensionRoot, '../../..')
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lnbits-tpos-e2e-'))
const extensionDir = path.join(dataDir, 'extensions')

fs.mkdirSync(extensionDir, {recursive: true})

copyExtension(extensionRoot, path.join(extensionDir, 'tpos'))
copyExtension(
  path.join(lnbitsRoot, 'lnbits', 'extensions', 'tabs'),
  path.join(extensionDir, 'tabs')
)

const baseUrl = new URL(
  process.env.LNBITS_E2E_BASE_URL ?? 'http://127.0.0.1:5019'
)
const host = baseUrl.hostname
const port = baseUrl.port || '5019'

const server = childProcess.spawn(
  'uv',
  [
    'run',
    'uvicorn',
    'lnbits.__main__:app',
    '--host',
    host,
    '--port',
    port,
    '--log-level',
    'warning'
  ],
  {
    cwd: lnbitsRoot,
    env: {
      ...process.env,
      AUTH_HTTPS_ONLY: 'false',
      DEBUG: 'true',
      HOST: host,
      LNBITS_ADMIN_UI: 'true',
      LNBITS_BACKEND_WALLET_CLASS: 'FakeWallet',
      LNBITS_DATABASE_URL: '',
      LNBITS_DATA_FOLDER: dataDir,
      LNBITS_EXTENSIONS_PATH: dataDir,
      LNBITS_ENABLE_LOG_TO_FILE: 'false',
      LNBITS_EXTENSIONS_MANIFESTS: '[]',
      PORT: port,
      PYTHONUNBUFFERED: '1',
      LNBITS_WASM_EXTENSIONS_MANIFESTS: '[]',
      UV_CACHE_DIR: path.join(dataDir, 'uv-cache')
    },
    stdio: 'inherit'
  }
)

let shuttingDown = false
function shutdown(signal) {
  if (shuttingDown) return
  shuttingDown = true
  if (server.exitCode === null) server.kill(signal)
}

process.on('SIGTERM', () => shutdown('SIGTERM'))
process.on('SIGINT', () => shutdown('SIGINT'))
server.on('exit', (code, signal) => {
  if (!shuttingDown) process.exit(code ?? (signal ? 1 : 0))
})

function copyExtension(source, destination) {
  fs.cpSync(source, destination, {
    recursive: true,
    filter: sourcePath => {
      const relative = path.relative(source, sourcePath)
      if (!relative) return true
      const firstPart = relative.split(path.sep)[0]
      return ![
        '.git',
        '.mypy_cache',
        '.pytest_cache',
        '.ruff_cache',
        '.venv',
        '__pycache__',
        'data',
        'node_modules',
        'tests',
        'test-reports'
      ].includes(firstPart)
    }
  })
}
