window.app.component('item-list', {
  name: 'item-list',
  props: ['items', 'inclusive', 'format', 'currency', 'add-product'],
  data: function () {
    return {}
  },
  computed: {
  },
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
  <div style="margin-bottom: 200px">
    <q-separator></q-separator>
    <q-list separator padding>
      <q-item v-for="item in items" :key="item.id" class="q-py-md">
        <q-item-section avatar top>
          <q-avatar>
            <img v-if="item.image" class="responsive-img" :src="item.image" />
            <q-icon v-else color="primary" name="sell"></q-icon>
          </q-avatar>
        </q-item-section>

        <q-item-section class="col-4">
          <q-item-label class="ellipsis"
            ><span
              class="text-body text-weight-bold text-uppercase"
              v-text="item.title"
            ></span
          ></q-item-label>
          <q-item-label lines="1">
            <span
              class="text-weight-medium ellipsis"
              v-text="item.description"
            ></span>
          </q-item-label>
        </q-item-section>

        <q-item-section top>
          <q-item-label lines="1">
            <span class="text-weight-bold" v-text="item.formattedPrice"></span>
          </q-item-label>
          <q-item-label caption lines="1">
            <i
              ><span
                v-text="taxString(item)"
              ></span
            ></i>
          </q-item-label>
          <q-item-label v-if="!inclusive" lines="1" class="q-mt-xs">
            <span
              v-text="formatPrice(item)"
            ></span>
          </q-item-label>
        </q-item-section>

        <q-item-section side>
          <div class="text-grey-8 q-gutter-sm">
            <q-btn round color="green" icon="add" @click="addToCart(item)" />
          </div>
        </q-item-section>
      </q-item>
    </q-list>
    <q-separator></q-separator>
  </div>
  `
})
