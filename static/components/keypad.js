window.app.component('keypad-item', {
  name: 'keypad-item',
  props: ['value'],
  template: `
    <q-btn
        unelevated
        size="xl"
        :outline="!($q.dark.isActive)"
        rounded
        color="primary"
        :disable="keypadDisabled"
        :label="value"
        ></q-btn>
    `
})

window.app.component('keypad', {
  name: 'keypad',
  data() {
    return {}
  },
  computed: {},
  methods: {
    taxString(item) {
      return `tax ${this.inclusive ? 'incl.' : 'excl.'} ${item.tax ? item.tax + '%' : ''}`
    },
    formatPrice(item) {
      return `Price w/ tax: ${this.format(item.price * (1 + item.tax * 0.01), this.currency)}`
    },
    addToCart(item) {
      this.$emit('add-product', item)
    }
  },
  template: `
  <div class="keypad q-pa-sm">
    <slot></slot>
  </div>
  `
})
