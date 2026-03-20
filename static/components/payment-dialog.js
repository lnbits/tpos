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
    currentCopyValue() {
      if (this.tab === 'uqr' && this.dialogData?.unified_qr) {
        return this.dialogData.unified_qr
      }
      if (this.tab === 'btc' && this.dialogData?.onchain_address) {
        return this.dialogData.onchain_address
      }
      return this.lightningValue
    },
    onchainHref() {
      return this.dialogData?.onchain_address
        ? `bitcoin:${this.dialogData.onchain_address}`
        : ''
    }
  },
  watch: {
    dialogData: {
      handler() {
        if (this.hasUnifiedQr) {
          this.tab = 'uqr'
          return
        }
        if (this.hasLightning) {
          this.tab = 'ln'
          return
        }
        if (this.hasOnchain) {
          this.tab = 'btc'
        }
      },
      immediate: true,
      deep: true
    }
  },
  methods: {
    copyCurrentValue() {
      if (this.currentCopyValue) {
        this.$emit('copy', this.currentCopyValue)
      }
    }
  },
  template: `
    <q-card-section class="q-pa-none">
      <q-tabs
        v-if="hasUnifiedQr || hasLightning || hasOnchain"
        v-model="tab"
        dense
        align="justify"
        narrow-indicator
        inline-label
        class="q-mb-md"
      >
        <q-tab
          v-if="hasUnifiedQr"
          name="uqr"
          icon="qr_code"
          label="UQR (BIP21)"
        ></q-tab>
        <q-tab
          v-if="hasLightning"
          name="ln"
          icon="bolt"
          label="Lightning"
        ></q-tab>
        <q-tab
          v-if="hasOnchain"
          name="btc"
          icon="link"
          label="Onchain"
        ></q-tab>
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
          <q-list dense class="q-mb-md">
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
              </q-item-section>
            </q-item>
          </q-list>
          <lnbits-qrcode
            :show-buttons="false"
            :value="dialogData.onchain_address"
            :href="onchainHref"
          ></lnbits-qrcode>
        </q-tab-panel>
      </q-tab-panels>
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
      <q-chip v-if="nfcTagReading" square>
        <q-avatar icon="nfc" color="positive" text-color="white"></q-avatar>
        NFC supported
      </q-chip>
      <div v-else class="text-caption text-grey" v-text="'NFC not supported'"></div>
    </q-card-section>

    <q-card-actions align="between" class="q-pt-none">
      <q-btn
        outline
        color="grey"
        @click="copyCurrentValue()"
        :disable="!currentCopyValue"
        label="Copy invoice"
      ></q-btn>
      <q-btn v-close-popup flat color="grey" label="Close"></q-btn>
    </q-card-actions>
  `
})
