window.app.component('receipt', {
  name: 'receipt',
  props: ['data'],
  data() {
    return {
      currency: null,
      exchangeRate: null
    }
  },
  computed: {
    cartSubtotal() {
      let subtotal = 0
      if (!this.data.extra.details || !this.data.extra.details.items) { 
        subtotal = this.fiatAmount(this.data.extra.amount)
      } else {
        this.data.extra.details.items.forEach(item => {
          subtotal += item.price * item.quantity
        })
      }
      return subtotal
    },
    cartTotal() {
      let total = 0
      if (!this.data.extra.details || !this.data.extra.details.items) {
          return this.fiatAmount(this.data.extra.amount)
      }
      this.data.extra.details.items.forEach(item => {
        total += item.price * item.quantity
      })
      if (this.data.extra.details.taxIncluded) {
        return total
      }
      return total + this.data.extra.details.taxValue
    },
    exchangeRateInfo() {
      if (!this.exchangeRate) {
        return 'Exchange rate not available'
      }
      return `Rate (sat/${this.currency}): ${this.exchangeRate.toFixed(2)}`
    },
    formattedDate() {
      return LNbits.utils.formatDateString(this.data.created_at)
    },
    businessAddress() {
      return this.data.business_address.split('\n')
    },
    currencyText() {
      return `(${this.currency})`
    }
  },
  methods: {
    fiatAmount(amount) {
      if (!this.exchangeRate) {
        return amount
      }
      return (amount / this.exchangeRate)
    }
  },
  created() {
    this.currency = this.data.extra.details.currency || LNBITS_DENOMINATION
    this.exchangeRate = this.data.extra.details.exchangeRate || 1
    console.log('Receipt component created', this.data)
  },
  template: `
    <div class="q-pa-md">
    <div class="text-center q-mb-xl">
    <p class='text-h6 text-uppercase'>Receipt</p>
    <p class=''><span v-text="formattedDate"></span></p>
    <p class=''><span v-text="exchangeRateInfo"></span></p>
    </div>
    <div v-if=data.business_name>
      <span v-text="data.business_name"></span>
    </div>
    <div v-if=data.business_address v-for="line in businessAddress">
    <span v-text="line"></span>
    </div>
    <div v-if=data.business_vat_id class=q-mb-xl>
    <span class="text-h6 text-uppercase">VAT: </span>
      <span v-text="data.business_vat_id"></span>
    </div>
    <q-table v-if="data.extra.details.items && data.extra.details.items.length > 0"
      :rows="data.extra.details.items"
      :columns="[
          { name: 'title', label: 'Item', field: 'title' },
          { name: 'formattedPrice', label: 'Unit Price', field: 'formattedPrice', align: 'right' },
          { name: 'quantity', label: 'Quantity', field: 'quantity' },
        ]"
      row-key="title"
        hide-bottom
    >
    </q-table>
    <div class="q-my-xl q-gutter-md">
      <div class="row">
        <div class="col-6">
          <span>Subtotal </span>
          <span v-if="currency != 'sats'" v-text="currencyText"></span>
        </div>
        <div class="col-6 text-right">
        <span v-text="cartSubtotal.toFixed(2)"></span>
        </div>
      </div>
      <div class="row">
        <div class="col-6">
          <span>Tax </span>
          <span v-if="currency != 'sats'" v-text="currencyText"></span>
        </div>
        <div class="col-6 text-right">
        <span v-text="data.extra.details.taxValue.toFixed(2)"></span>
        </div>
      </div>
      <div class="row">
        <div class="col-6">
        <span>Total </span>
          <span v-if="currency != 'sats'" v-text="currencyText"></span>
        </div>
        <div class="col-6 text-right">
        <span v-text="cartTotal.toFixed(2)"></span>
        </div>
      </div>
      <div class="row">
        <div class="col-6">Total (sats)</div>
        <div class="col-6 text-right">
        <span v-text="data.extra.amount"></span>
        </div>
      </div>
    </div>
    <div class="text-center q-mb-xl">
    <p class='text-h6 text-uppercase'>
    Thank you for your purchase! 
    </p>
    </div>
  </div>
    `
})
