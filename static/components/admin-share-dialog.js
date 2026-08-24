window.app.component('tpos-admin-share-dialog', {
  name: 'tpos-admin-share-dialog',
  props: ['dialog', 'buildShareUrl'],
  emits: ['warm-wrapper-assetlinks', 'generate-wrapper-token', 'copy-url'],
  template: `
    <q-dialog v-model="dialog.show" position="top">
      <q-card class="q-pa-lg q-pt-xl lnbits__dialog-card">
        <lnbits-qrcode :value="buildShareUrl(dialog.data)"></lnbits-qrcode>
        <div class="text-center q-mb-xl">
          <p style="word-break: break-all">
            <strong v-text="dialog.data.name"></strong>
          </p>
        </div>
        <div class="row q-mt-lg">
          <div class="col">
            <q-toggle
              v-model="dialog.data.useWrapper"
              label="Use with TPoS-Wrapper Android app"
              @update:model-value="$emit('warm-wrapper-assetlinks', $event)"
            ></q-toggle>
          </div>
        </div>
        <div class="row q-mt-sm" v-if="dialog.data.useWrapper">
          <div class="col">
            <q-btn
              outline
              color="primary"
              type="a"
              href="https://github.com/lnbits/TPoS-Stripe-Tap-to-Pay-Wrapper-Stripev5"
              target="_blank"
              rel="noopener noreferrer"
              label="GET THE TPOS-WRAPPER"
            ></q-btn>
          </div>
        </div>
        <div
          v-if="dialog.data.useWrapper && dialog.data.stripe_card_payments"
        >
          <div class="row q-mt-lg">
            <div class="col">
              <q-input
                v-model="dialog.data.posLocation"
                label="PoS Stripe Location"
              >
              </q-input>
            </div>
            <div class="col q-pl-md">
              <small>
                If accepting Stripe payments, visit
                https://dashboard.stripe.com/terminal and grab a new location ID
              </small>
            </div>
          </div>
          <div class="row q-mt-lg">
            <div class="col">
              <q-btn
                outline
                color="primary"
                :disable="!dialog.data.posLocation"
                :loading="dialog.data.loadingWrapperToken"
                @click="$emit('generate-wrapper-token', dialog.data)"
                label="Generate ACL token"
              ></q-btn>
            </div>
            <div class="col">
              <small>Press if accepting Stripe payments.</small>
            </div>
          </div>
        </div>
        <div class="row q-mt-lg">
          <q-btn
            outline
            color="grey"
            :disable="dialog.data.loadingWrapperToken"
            @click="$emit('copy-url', buildShareUrl(dialog.data))"
            >Copy URL</q-btn
          >
          <q-btn v-close-popup flat color="grey" class="q-ml-auto">Close</q-btn>
        </div>
      </q-card>
    </q-dialog>
  `
})
