window.app.component('tpos-admin-form-dialog', {
  name: 'tpos-admin-form-dialog',
  props: [
    'dialog',
    'g',
    'currencyOptions',
    'hasFiatProvider',
    'fiatProviders',
    'isFiatCurrency',
    'onchainStatus',
    'onchainWalletOptions',
    'withdrawOptions',
    'createOrUpdateDisabled'
  ],
  emits: ['close', 'submit'],
  template: `
  <q-dialog v-model="dialog.show" position="top" @hide="$emit('close')">
    <q-card class="q-pa-lg q-pt-xl" style="width: 500px">
      <q-form @submit="$emit('submit')" class="q-gutter-md">
        <q-input
          filled
          dense
          v-model.trim="dialog.data.name"
          label="Name *"
          placeholder="Tiago's PoS"
        ></q-input>
        <q-select
          filled
          dense
          emit-value
          v-model="dialog.data.wallet"
          :options="g.user.walletOptions"
          label="Wallet *"
        ></q-select>
        <q-select
          v-if="g.settings.denomination == 'sats'"
          filled
          dense
          emit-value
          v-model="dialog.data.currency"
          :options="currencyOptions"
          label="Currency *"
        ></q-select>
        <q-select
          v-if="dialog.data.currency != g.settings.denomination && hasFiatProvider"
          filled
          dense
          emit-value
          v-model="dialog.data.fiat_provider"
          :options="fiatProviders"
          label="Fiat payments"
          hint="Choose what fiat platform to use for fiat payments."
        >
          <template v-if="dialog.data.fiat_provider" v-slot:append>
            <q-icon
              name="cancel"
              @click.stop.prevent="dialog.data.fiat_provider = null"
              class="cursor-pointer"
            />
          </template>
        </q-select>
        <div
          v-if="(dialog.data.fiat_provider || []).includes('stripe')"
          class="row"
        >
          <div class="col">
            <q-checkbox
              v-model="dialog.data.stripe_card_payments"
              label="Allow Stripe card payments"
            >
              <q-tooltip>
                <span
                  v-text="'If enabled, invoices can be paid by tapping a card.'"
                ></span
              ></q-tooltip>
            </q-checkbox>
          </div>
          <div class="col">
            <small>
              After saving use the QR code button to create the pairing code.
            </small>
          </div>
        </div>
        <div
          v-if="
            (dialog.data.fiat_provider || []).includes('stripe') &&
              dialog.data.stripe_card_payments
          "
          class="row q-mt-sm"
        >
          <div class="col">
            <q-input
              filled
              dense
              v-model.trim="dialog.data.stripe_reader_id"
              label="Stripe reader ID"
              hint="If you are using a Stripe reader (rather than a generic device) add it here"
            ></q-input>
          </div>
        </div>
        <div v-if="g.user.super_user" class="row q-mt-sm">
          <div class="col">
            <q-checkbox
              v-model="dialog.data.allow_cash_settlement"
              :disable="!isFiatCurrency"
              label="Allow cash settlement"
            >
              <q-tooltip v-if="!isFiatCurrency">
                currency must be set to fiat
              </q-tooltip>
            </q-checkbox>
          </div>
        </div>
        <div v-if="g.user.super_user" class="row q-mt-sm">
          <div class="col">
            <q-checkbox
              v-model="dialog.data.onchain_enabled"
              :disable="!onchainStatus.available"
              label="Enable onchain payments"
            >
              <q-tooltip v-if="!onchainStatus.available">
                <span
                  v-text="onchainStatus.message || 'Watchonly extension must be enabled and working.'"
                ></span>
              </q-tooltip>
            </q-checkbox>
          </div>
        </div>
        <div v-if="dialog.data.onchain_enabled" class="row q-col-gutter-md">
          <div class="col-12">
            <q-banner
              v-if="!onchainStatus.available"
              dense
              rounded
              class="bg-orange-2 text-dark"
            >
              <span
                v-text="onchainStatus.message || 'Watchonly extension must be enabled and working.'"
              ></span>
            </q-banner>
          </div>
          <div class="col-12">
            <q-select
              filled
              dense
              emit-value
              map-options
              v-model="dialog.data.onchain_wallet_id"
              :options="onchainWalletOptions"
              :disable="!onchainStatus.available"
              label="Watchonly wallet *"
              hint="Select the watchonly wallet used to derive fresh onchain addresses."
            ></q-select>
          </div>
          <div class="col-12">
            <q-toggle
              v-model="dialog.data.onchain_zero_conf"
              label="Accept onchain payment at 0-conf"
            >
              <q-tooltip>
                If disabled, TPoS waits for the first confirmation before
                completing the sale.
              </q-tooltip>
            </q-toggle>
          </div>
        </div>
        <div class="row">
          <div class="col">
            <q-checkbox
              v-model="dialog.data.lnaddress"
              label="LNaddress funding"
            >
              <q-tooltip>
                <span
                  v-text="'If enabled, the tpos can be funded with a LNaddress in the public page.'"
                ></span
              ></q-tooltip>
            </q-checkbox>
          </div>
          <div class="col">
            <q-input
              filled
              dense
              :disable="!dialog.data.lnaddress"
              v-model="dialog.data.lnaddress_cut"
              type="number"
              label="LNaddress cut 0-100 percent"
              ><q-tooltip>
                <span
                  v-text="'Percent charge for using the LNaddress funding option. To go to the TPoS wallet.'"
                ></span
              ></q-tooltip>
            </q-input>
          </div>
        </div>
        <div class="row">
          <div class="col">
            <q-checkbox
              v-model="dialog.advanced.tips"
              label="Enable tips"
            ></q-checkbox>
          </div>
          <div class="col">
            <q-checkbox
              v-model="dialog.advanced.otc"
              label="Enable selling BTC (ATM)"
            ></q-checkbox>
          </div>
        </div>
        <div class="row">
          <div class="col">
            <q-checkbox
              v-model="dialog.data.enable_receipt_print"
              label="Enable printing (experimental)"
            ></q-checkbox>
          </div>
          <div class="col">
            <q-checkbox
              v-model="dialog.data.enable_remote"
              label="Enable remote (to trigger payments on external device)"
            ></q-checkbox>
          </div>
        </div>
        <div class="row">
          <div class="col">
            <q-checkbox
              v-model="dialog.data.allow_price_adjustment"
              label="Allow cart item price adjustment"
            ></q-checkbox>
          </div>
        </div>
        <div class="row">
          <div class="col">
            <q-checkbox
              v-model="dialog.data.tabs_enabled"
              label="Enable Tabs integration"
            ></q-checkbox>
          </div>
          <div class="col">
            <q-checkbox
              v-model="dialog.data.tabs_allow_create"
              :disable="!dialog.data.tabs_enabled"
              label="Allow creating tabs from TPoS"
            ></q-checkbox>
          </div>
        </div>
        <template v-if="dialog.data.enable_receipt_print">
          <p class="text-caption">
            Receipt printing is an experimental feature. Not all devices work
            correctly, or work at all.
          </p>
          <q-input
            filled
            dense
            v-model="dialog.data.business_name"
            label="Business name"
            placeholder="My Shop"
          ></q-input>
          <q-input
            v-model="dialog.data.business_address"
            filled
            label="Business address"
            placeholder="123 Main St, City, Country"
            type="textarea"
          ></q-input>
          <q-input
            filled
            dense
            v-model="dialog.data.business_vat_id"
            label="VAT ID"
            placeholder="123456789"
          ></q-input>
          <q-checkbox
            v-model="dialog.data.only_show_sats_on_bitcoin"
            label="Only show sats & sats conversion rate on bitcoin transactions"
          ></q-checkbox>
        </template>
        <template v-if="dialog.advanced.tips">
          <q-select
            filled
            dense
            emit-value
            v-model="dialog.data.tip_wallet"
            :options="g.user.walletOptions"
            label="Tip Wallet"
          ></q-select>
          <q-select
            filled
            multiple
            dense
            emit-value
            v-model="dialog.data.tip_options"
            v-if="dialog.data.tip_wallet"
            use-input
            use-chips
            hide-dropdown-icon
            input-debounce="0"
            inputmode="numeric"
            new-value-mode="add-unique"
            label="Tip % Options (hit enter to add values)"
            ><q-tooltip>Hit enter to add values</q-tooltip>
            <template v-slot:hint>
              You can leave this blank. A default rounding option is available
              (round amount to a value)
            </template>
          </q-select>
        </template>
        <template v-if="dialog.advanced.otc">
          <q-input
            filled
            dense
            v-model.number="dialog.data.withdraw_limit"
            type="number"
            :label="'Max amount to be sold daily (' + g.settings.denomination + ') *'"
          ></q-input>

          <div class="row">
            <div class="col-8">
              <q-input
                filled
                dense
                v-model.number="dialog.data.withdraw_between"
                type="number"
                min="0"
                :label="'Time between withdraws (' + g.settings.denomination + ')'"
              ></q-input>
            </div>
            <div class="col-4">
              <q-option-group
                v-model="dialog.data.withdraw_time_option"
                :options="withdrawOptions"
                color="primary"
                inline
              ></q-option-group>
            </div>
          </div>

          <q-input
            filled
            dense
            v-model.number="dialog.data.withdraw_premium"
            type="number"
            label="Withdraw premium %"
            step="0.01"
            min="0"
          ></q-input>
        </template>
        <q-list>
          <q-expansion-item
            expand-separator
            icon="inventory"
            label="Tax settings"
            caption="Only applicable when using items/products"
          >
            <q-card>
              <q-card-section>
                Tax Inclusive means the unit price includes tax. (default)
                <br />
                Tax Exclusive means tax is applied on top of the unit price.
              </q-card-section>
              <q-card-section>
                <div class="row q-mt-lg">
                  <div class="col-12 col-md-6">
                    <q-toggle
                      v-model="dialog.data.tax_inclusive"
                      :label="dialog.data.tax_inclusive ?
                                  'Tax Inclusive' :
                                  'Tax Exclusive'"
                      ><q-tooltip>
                        <span
                          v-text="'Applies to all products'"
                        ></span> </q-tooltip
                    ></q-toggle>
                  </div>
                  <div class="col-12 col-md-6">
                    <q-input
                      filled
                      dense
                      v-model.number="dialog.data.tax_default"
                      type="number"
                      label="Default tax rate %"
                      step="0.01"
                      min="0"
                      max="100"
                      hint="Fallback tax rate if not set on product"
                    ></q-input>
                  </div>
                </div>
              </q-card-section>
            </q-card>
          </q-expansion-item>
        </q-list>
        <div class="row q-mt-lg">
          <q-btn
            v-if="dialog.data.id"
            unelevated
            color="primary"
            type="submit"
            :disable="createOrUpdateDisabled"
            >Update TPoS</q-btn
          >
          <q-btn
            v-else
            unelevated
            color="primary"
            :disable="createOrUpdateDisabled"
            type="submit"
            >Create TPoS</q-btn
          >
          <q-btn v-close-popup flat color="grey" class="q-ml-auto"
            >Cancel</q-btn
          >
        </div>
      </q-form>
    </q-card>
  </q-dialog>
  `
})
