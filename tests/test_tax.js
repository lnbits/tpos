const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const root = path.resolve(__dirname, '..')
const sandbox = {
  Intl,
  Math,
  Number,
  Object,
  Array,
  Date,
  JSON,
  Map,
  Set,
  Promise,
  console,
  windowMixin: {},
  tpos: {},
  i18n: {global: {locale: 'en-US'}},
  Vue: {
    createApp(config) {
      return config
    }
  }
}
sandbox.window = sandbox
sandbox.globalThis = sandbox

const context = vm.createContext(sandbox)
for (const file of ['static/js/tpos-utils.js', 'static/js/tpos.js']) {
  vm.runInContext(fs.readFileSync(path.join(root, file), 'utf8'), context, {
    filename: file
  })
}

const cartTaxTotal = sandbox.app.methods.cartTaxTotal

function taxTotal({items, taxDefault = 0, taxInclusive}) {
  const state = {
    cart: new Map(items.map((item, index) => [item.id || index, item])),
    taxDefault,
    taxInclusive,
    currency: 'EUR',
    cartTax: 0
  }
  cartTaxTotal.call(state)
  return state.cartTax
}

assert.equal(
  taxTotal({
    items: [{price: 3.5, quantity: 1, tax: 21}],
    taxInclusive: true
  }),
  0.61
)
assert.equal(
  taxTotal({
    items: [{price: 3.5, quantity: 1, tax: 21}],
    taxInclusive: false
  }),
  0.74
)
assert.equal(
  taxTotal({
    items: [{price: 3.5, quantity: 2, tax: 21}],
    taxInclusive: true
  }),
  1.21
)
assert.equal(
  taxTotal({
    items: [
      {price: 10, quantity: 1, tax: 10},
      {price: 5, quantity: 2, tax: 20}
    ],
    taxInclusive: true
  }),
  2.58
)
assert.equal(
  taxTotal({
    items: [{price: 3.5, quantity: 1, tax: 0}],
    taxDefault: 0,
    taxInclusive: true
  }),
  0
)
assert.equal(
  taxTotal({
    items: [{price: 3.5, quantity: 1}],
    taxDefault: 21,
    taxInclusive: true
  }),
  0.61
)

console.log('Tax calculation tests passed')
