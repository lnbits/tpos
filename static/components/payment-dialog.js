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
  data() {
    return {
      tab: 'ln'
    }
  },
  computed: {
    hasUnifiedQr() {
      return Boolean(this.dialogData?.unified_qr)
    },
    hasLightning() {
      return Boolean(this.lightningValue)
    },
    hasOnchain() {
      return Boolean(this.dialogData?.onchain_address)
    },
    lightningValue() {
      if (this.dialogData?.lightning_payment_request) {
        return this.dialogData.lightning_payment_request
      }
      if (
        this.dialogData?.payment_request &&
        (this.dialogData.payment_request.startsWith('lightning:') ||
          this.dialogData.payment_request.toUpperCase().startsWith('LNURL'))
      ) {
        return this.dialogData.payment_request
      }
      return null
    }
  },
  watch: {
    dialogData: {
      handler() {
        if (this.hasUnifiedQr) {
          this.tab = 'uqr'
        } else if (this.hasLightning) {
          this.tab = 'ln'
        } else if (this.hasOnchain) {
          this.tab = 'btc'
        }
      },
      immediate: true,
      deep: true
    }
  },
  methods: {
    copyCurrentValue() {
      if (this.tab === 'uqr' && this.dialogData?.unified_qr) {
        this.$emit('copy', this.dialogData.unified_qr)
      } else if (this.tab === 'btc' && this.dialogData?.onchain_address) {
        this.$emit('copy', this.dialogData.onchain_address)
      } else if (this.lightningValue) {
        this.$emit('copy', this.lightningValue)
      }
    }
  },
  template: `
    <div :class="isMobileLandscaped ? 'row flex-center' : ''">
      <div class="full-width">
        <q-tabs
          v-if="hasUnifiedQr || hasLightning || hasOnchain"
          v-model="tab"
          dense
          class="text-grey q-mb-md"
          active-color="primary"
          indicator-color="primary"
          align="justify"
          narrow-indicator
          inline-label
        >
          <q-tab v-if="hasUnifiedQr" name="uqr" icon="qr_code" label="UQR (BIP21)"></q-tab>
          <q-tab v-if="hasLightning" name="ln" icon="bolt" label="Lightning"></q-tab>
          <q-tab v-if="hasOnchain" name="btc" icon="link" label="Onchain"></q-tab>
        </q-tabs>

        <q-tab-panels v-model="tab" animated style="background: none">
          <q-tab-panel name="uqr" v-if="hasUnifiedQr" class="q-pa-none">
            <lnbits-qrcode
              :show-buttons="false"
              :value="dialogData.unified_qr"
              :href="dialogData.unified_qr"
            ></lnbits-qrcode>
          </q-tab-panel>

          <q-tab-panel name="ln" v-if="hasLightning" class="q-pa-none">
            <lnbits-qrcode
              :show-buttons="false"
              :value="lightningValue"
              :href="lightningValue"
            ></lnbits-qrcode>
          </q-tab-panel>

          <q-tab-panel name="btc" v-if="hasOnchain" class="q-pa-none">
            <div class="text-center q-mb-md">
              <a
                class="text-secondary"
                style="color: unset; word-break: break-all"
                :href="'bitcoin:' + dialogData.onchain_address"
              >
                <span v-text="dialogData.onchain_address"></span>
              </a>
            </div>
            <lnbits-qrcode
              :show-buttons="false"
              :value="dialogData.onchain_address"
              :href="'bitcoin:' + dialogData.onchain_address"
            ></lnbits-qrcode>
          </q-tab-panel>
        </q-tab-panels>
      </div>
      <div class="text-center full-width">
        <h3 class="q-my-md">${ activePaymentAmountWithTipFormatted }</h3>
        <h5 class="q-mt-none q-mb-sm">
          ${ activePaymentAmountFormatted }
          <span v-show="tipOptions" style="font-size: 0.75rem">(+ ${ tipAmountFormatted } tip)</span>
        </h5>
        <q-chip v-if="nfcTagReading" square>
          <q-avatar icon="nfc" color="positive" text-color="white"></q-avatar>
          NFC supported
        </q-chip>
        <span v-else class="text-caption text-grey">NFC not supported</span>
      </div>
      <div class="row q-mt-lg full-width">
        <q-btn outline color="grey" @click="copyCurrentValue()">Copy invoice</q-btn>
        <q-btn v-close-popup flat color="grey" class="q-ml-auto">Close</q-btn>
      </div>
    </div>
  `
})
