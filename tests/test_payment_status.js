const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

let component
let socket
let status = {paid: false}
let failure = false
let finish = 0
const requests = []
class FakeWebSocket {
  constructor() {
    socket = this
  }
  close() {}
}
vm.runInNewContext(
  fs.readFileSync(path.join(__dirname, '../static/js/tpos.js'), 'utf8'),
  {
    window: {
      tposUtils: {},
      location: {protocol: 'https:', origin: 'https://pos.example'}
    },
    Vue: {createApp: value => (component = value)},
    URL,
    WebSocket: FakeWebSocket,
    setInterval: () => 1,
    clearInterval() {},
    console: {warn() {}},
    LNbits: {
      api: {
        async request(method, url) {
          requests.push([method, url])
          if (failure) throw new Error('Unavailable')
          return {data: status}
        }
      }
    },
    Quasar: {Notify: {create() {}}}
  }
)

async function main() {
  const state = {
    ...component.methods,
    tposId: 'pos',
    paymentWsByHash: {},
    invoiceDialog: {show: true, data: {payment_hash: 'hash'}},
    clearCart() {},
    showComplete() {
      finish++
    }
  }
  state.subscribeToPaymentWS('hash')
  await socket.onmessage({data: '{"pending":false,"payment_hash":"wrong"}'})
  assert.equal(finish, 0)
  assert.equal(state.invoiceDialog.show, true)
  assert.equal(requests[0][1], '/tpos/api/v1/tposs/pos/invoices/hash')
  await socket.onmessage({data: '{"status":"expired"}'})
  assert.equal(state.invoiceDialog.show, true)
  failure = true
  await socket.onmessage({data: 'malformed'})
  assert.equal(finish, 0)
  failure = false
  status = {paid: true}
  await Promise.all([
    state.checkPaymentStatus('hash'),
    socket.onmessage({data: 'paid'})
  ])
  assert.equal(finish, 1)
  assert.equal(state.invoiceDialog.show, false)
  state.invoiceDialog = {show: true, data: {payment_hash: 'new-hash'}}
  await state.checkPaymentStatus('hash')
  assert.equal(finish, 1)
  assert.equal(state.invoiceDialog.show, true)
  status = {paid: false, status: 'underpaid'}
  await state.checkPaymentStatus('new-hash')
  assert.equal(state.invoiceDialog.show, false)
  state.paymentAmount = 5.4
  state.buildInvoiceParams = () => ({amount: 5400})
  state.openInvoiceDialog = () => {}
  state.subscribeToPaymentWS = () => {}
  status = {payment_hash: 'quote', extra: {amount_fiat: 5.42}}
  await state.showInvoice()
  assert.equal(state.paymentAmount, 5.42)
  console.log('Payment status verification tests passed')
}
main().catch(error => {
  console.error(error)
  process.exitCode = 1
})
