window.app.component('tpos-payment-dialog', {
  name: 'tpos-payment-dialog',
  props: [
    'dialogData',
    'isMobileLandscaped',
    'activePaymentAmountFormatted',
    'activePaymentAmountWithTipFormatted',
    'tipOptions',
    'tipAmountFormatted',
    'nfcTagReading'
  ],
  emits: ['copy'],
  computed: {
    isOnchain() {
      return this.dialogData?.payment_method === 'onchain'
    },
    onchainUri() {
      const address = this.dialogData?.onchain_address
      if (!address) return null

      const params = new URLSearchParams()
      const satAmount = Number(this.dialogData?.onchain_amount_sat || 0)

      if (satAmount > 0) {
        const btcAmount = (satAmount / 100000000)
          .toFixed(8)
          .replace(/\.?0+$/, '')
        params.set('amount', btcAmount)
      }

      if (tpos?.name) {
        params.set('label', `LNbits TPoS ${tpos.name}`)
      } else {
        params.set('label', 'LNbits TPoS')
      }

      params.set('message', 'Thank you for your order')

      const query = params.toString()
      return `bitcoin:${address}${query ? `?${query}` : ''}`
    },
    qrValue() {
      if (this.isOnchain) {
        return this.onchainUri
      }
      return this.dialogData?.lightning_payment_request || null
    },
    amountSummary() {
      return this.activePaymentAmountFormatted || ''
    },
    totalSummary() {
      return this.activePaymentAmountWithTipFormatted || ''
    },
    tipSummary() {
      return this.tipOptions ? `(+ ${this.tipAmountFormatted} tip)` : ''
    },
    onchainHref() {
      return this.onchainUri || ''
    }
  },
  methods: {
    copyCurrentValue() {
      if (this.qrValue) {
        this.$emit('copy', this.qrValue)
      }
    }
  },
  template: `
    <q-card-section class="q-pa-none">
      <q-list v-if="isOnchain" dense class="q-mb-md">
        <q-item class="justify-center">
          <q-item-section class="items-center">
            <q-item-label lines="2" class="text-center">
              <a
                class="text-secondary"
                style="word-break: break-all"
                :href="onchainHref"
                v-text="dialogData.onchain_address"
              ></a>
            </q-item-label>
            <q-item-label
              v-if="dialogData.onchain_amount_sat"
              class="text-subtitle2 q-mt-sm"
              v-text="dialogData.onchain_amount_sat + ' sats'"
            ></q-item-label>
          </q-item-section>
        </q-item>
      </q-list>

      <lnbits-qrcode
        :show-buttons="false"
        :value="qrValue"
        :href="isOnchain ? onchainHref : qrValue"
      ></lnbits-qrcode>
    </q-card-section>

    <q-card-section class="text-center q-pt-md">
      <div class="text-h5 q-mb-sm" v-text="totalSummary"></div>
      <div class="text-subtitle1 q-mb-sm">
        <span v-text="amountSummary"></span>
        <span
          v-if="tipSummary"
          class="text-caption q-ml-xs"
          v-text="tipSummary"
        ></span>
      </div>
      <q-chip v-if="nfcTagReading && !isOnchain" square>
        <q-avatar icon="nfc" color="positive" text-color="white"></q-avatar>
        NFC supported
      </q-chip>
      <div
        v-else-if="!isOnchain"
        class="text-caption text-grey"
        v-text="'NFC not supported'"
      ></div>
    </q-card-section>

    <q-card-actions align="between" class="q-pt-none">
      <q-btn
        outline
        color="grey"
        @click="copyCurrentValue()"
        :disable="!qrValue"
        :label="isOnchain ? 'Copy address' : 'Copy invoice'"
      ></q-btn>
      <q-btn v-close-popup flat color="grey" label="Close"></q-btn>
    </q-card-actions>
  `
})
