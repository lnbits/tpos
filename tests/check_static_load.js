// Load-time smoke check for the TPoS frontend assets.
//
// Every script that a TPoS page loads is evaluated in a mocked browser
// environment, in the same order as the templates include them. This catches
// load-time failures (e.g. template literals interpolating undefined symbols
// when a component file is parsed) that the Python test suite cannot see.
//
// It also asserts that every custom component tag used in the page templates
// is actually registered by the loaded scripts.
//
// Run with: node tests/check_static_load.js

const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const root = path.resolve(__dirname, '..')

const pages = [
  {
    name: 'admin page (index.html)',
    templates: ['templates/tpos/index.html'],
    scripts: [
      'static/js/tpos-utils.js',
      'static/js/index.js',
      'static/components/admin-form-dialog.js',
      'static/components/admin-item-dialog.js',
      'static/components/admin-share-dialog.js',
      'static/components/admin-import-dialog.js'
    ]
  },
  {
    name: 'public tpos page (tpos.html)',
    templates: ['templates/tpos/tpos.html'],
    scripts: [
      'static/js/tpos-utils.js',
      'static/js/tpos.js',
      'static/components/item-list.js',
      'static/components/keypad.js',
      'static/components/payment-dialog.js',
      'static/components/held-carts-dialog.js',
      'static/components/print-dialog.js',
      'static/components/receipt.js',
      'static/components/order-receipt.js'
    ]
  }
]

// Tags provided outside the extension scripts: Quasar components (q-*),
// LNbits core components (lnbits-*, registered by the core bundle) and
// Vue built-ins.
function isBuiltinTag(tag) {
  return (
    tag.startsWith('q-') ||
    tag.startsWith('lnbits-') ||
    tag.startsWith('router-') ||
    [
      'transition',
      'transition-group',
      'component',
      'slot',
      'template'
    ].includes(tag)
  )
}

function templateWithIncludes(file, seen = new Set()) {
  if (seen.has(file)) {
    return ''
  }
  seen.add(file)
  const full = path.join(root, file)
  if (!fs.existsSync(full)) {
    return ''
  }
  const source = fs.readFileSync(full, 'utf8')
  let out = source
  const includeRe = /\{%\s*include\s+"(tpos\/[^"]+)"\s*%\}/g
  let match
  while ((match = includeRe.exec(source)) !== null) {
    out += templateWithIncludes(path.join('templates', match[1]), seen)
  }
  return out
}

function usedComponentTags(html) {
  const tags = new Set()
  const tagRe = /<([a-z][a-z0-9]*-[a-z0-9-]+)[\s>/]/g
  let match
  while ((match = tagRe.exec(html)) !== null) {
    const tag = match[1]
    if (!isBuiltinTag(tag)) {
      tags.add(tag)
    }
  }
  return tags
}

function makeSandbox() {
  const registered = {}
  const app = {
    component(name, config) {
      registered[name] = config
      return app
    },
    use: () => app,
    mixin: () => app,
    mount: () => app,
    directive: () => app,
    config: {}
  }
  const sandbox = {
    console,
    Intl,
    JSON,
    Date,
    Number,
    Math,
    Array,
    Object,
    String,
    Boolean,
    RegExp,
    Error,
    Promise,
    Set,
    Map,
    setInterval: () => 0,
    clearInterval: () => {},
    setTimeout: () => 0,
    clearTimeout: () => {},
    Vue: {
      createApp: () => app
    },
    Quasar: {date: {formatDate: () => ''}},
    LNbits: {utils: {notifyApiError: () => {}}},
    g: {settings: {denomination: 'sats'}, user: {}},
    tpos: {},
    document: {
      addEventListener: () => {},
      querySelector: () => null,
      getElementById: () => null
    },
    localStorage: {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {}
    },
    location: {protocol: 'http:', host: 'localhost'},
    navigator: {},
    windowMixin: {},
    __registered: registered
  }
  sandbox.window = sandbox
  sandbox.globalThis = sandbox
  return sandbox
}

let failures = 0

for (const page of pages) {
  const sandbox = makeSandbox()
  const context = vm.createContext(sandbox)

  for (const script of page.scripts) {
    const file = path.join(root, script)
    const code = fs.readFileSync(file, 'utf8')
    try {
      vm.runInContext(code, context, {filename: script})
      console.log(`ok    ${script}`)
    } catch (err) {
      failures += 1
      console.log(`FAIL  ${script}: ${err.message}`)
    }
  }

  const registered = sandbox.__registered
  for (const [name, config] of Object.entries(registered)) {
    if (!config || (config.template === undefined && !config.render)) {
      failures += 1
      console.log(`FAIL  component '${name}' registered without a template`)
    }
  }

  const html = page.templates.map(t => templateWithIncludes(t)).join('\n')
  for (const tag of usedComponentTags(html)) {
    if (registered[tag]) {
      console.log(`ok    <${tag}> is registered`)
    } else {
      failures += 1
      console.log(`FAIL  <${tag}> used in ${page.name} but never registered`)
    }
  }
}

if (failures > 0) {
  console.log(`\n${failures} static load check(s) failed`)
  process.exit(1)
}
console.log('\nAll static load checks passed')
