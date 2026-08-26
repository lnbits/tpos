window.app.component('tpos-payment-method-selector', {
  name: 'tpos-payment-method-selector',
  props: {
    bitcoinSymbol: {type: String, required: true},
    currency: {type: String, required: true},
    currencySymbol: {type: String, required: true},
    fiatProvider: {type: Boolean, default: false},
    tapToPayEnabled: {type: Boolean, default: false},
    allowCashSettlement: {type: Boolean, default: false},
    onchainEnabled: {type: Boolean, default: false},
    tabsEnabled: {type: Boolean, default: false},
    isSettlingTab: {type: Boolean, default: false},
    drawer: {type: Boolean, default: false},
    disabled: {type: Boolean, default: false}
  },
  emits: ['select'],
  template: `
    <div class="row q-col-gutter-sm q-pt-xs">
      <div class="col-6">
        <q-btn
          class="full-width q-px-lg q-py-sm"
          :size="drawer ? 'lg' : 'xl'"
          color="primary"
          rounded
          :disable="disabled"
          aria-label="Lightning Network"
          @click="$emit('select', 'btc')"
        >
          <div class="row items-center no-wrap q-gutter-x-sm">
            <span class="text-h4 text-weight-bold" v-text="bitcoinSymbol"></span>
            <span class="text-subtitle1 text-weight-medium">Lightning</span>
          </div>
        </q-btn>
      </div>
      <div class="col-6" v-if="onchainEnabled">
        <q-btn
          class="full-width q-px-lg q-py-sm"
          :size="drawer ? 'lg' : 'xl'"
          color="primary"
          rounded
          :disable="disabled"
          aria-label="Bitcoin Onchain"
          @click="$emit('select', 'btc_onchain')"
        >
          <div class="row items-center no-wrap q-gutter-x-sm">
            <span class="text-h4 text-weight-bold" v-text="bitcoinSymbol"></span>
            <span class="text-subtitle1 text-weight-medium">Onchain</span>
          </div>
        </q-btn>
      </div>
      <div class="col-6" v-if="fiatProvider">
        <q-btn
          class="full-width q-px-lg q-py-sm"
          :size="drawer ? 'lg' : 'xl'"
          color="secondary"
          rounded
          :disable="disabled"
          :aria-label="currency + ' QR'"
          @click="$emit('select', 'fiat')"
        >
          <div class="row items-center no-wrap q-gutter-x-xs">
            <span class="text-h5 text-weight-bold" v-text="currencySymbol"></span>
            <q-icon name="qr_code" size="30px"></q-icon>
          </div>
        </q-btn>
      </div>
      <div class="col-6" v-if="allowCashSettlement && currency != 'sats'">
        <q-btn
          class="full-width q-px-lg q-py-sm"
          :size="drawer ? 'lg' : 'xl'"
          color="secondary"
          rounded
          :disable="disabled"
          :aria-label="'Cash ' + currency"
          @click="$emit('select', 'cash')"
        >
          <div class="row items-center no-wrap q-gutter-x-xs">
            <span class="text-h5 text-weight-bold" v-text="currencySymbol"></span>
            <q-icon name="toll" size="30px"></q-icon>
          </div>
        </q-btn>
      </div>
      <div class="col-6" v-if="tapToPayEnabled">
        <q-btn
          class="full-width q-px-lg q-py-sm"
          :size="drawer ? 'lg' : 'xl'"
          color="secondary"
          rounded
          :disable="disabled"
          :aria-label="'Tap to Pay ' + currency"
          @click="$emit('select', 'fiat_tap')"
        >
          <div class="row items-center no-wrap q-gutter-x-xs">
            <span class="text-h5 text-weight-bold" v-text="currencySymbol"></span>
            <q-icon name="credit_card" size="30px"></q-icon>
          </div>
        </q-btn>
      </div>
      <div class="col-6" v-if="tabsEnabled && !isSettlingTab">
        <q-btn
          class="full-width q-px-lg q-py-sm"
          :size="drawer ? 'lg' : 'xl'"
          color="secondary"
          rounded
          :disable="disabled"
          aria-label="Add to tab"
          @click="$emit('select', 'tab')"
        >
          <div class="row items-center no-wrap q-gutter-x-xs">
            <q-icon class="text-h4" name="playlist_add_check"></q-icon>
            <span class="text-subtitle1 text-weight-medium">Tab</span>
          </div>
        </q-btn>
      </div>
    </div>
  `
})
