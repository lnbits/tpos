// LUD-24: PIN entry for NFC cards whose withdrawRequest carries a `pinLimit`.
window.app.component('tpos-pin-dialog', {
  name: 'tpos-pin-dialog',
  props: ['show', 'amount', 'attemptsLeft', 'error', 'loading'],
  emits: ['submit', 'cancel'],
  data() {
    return {
      pin: '',
      keys: ['1', '2', '3', '4', '5', '6', '7', '8', '9']
    }
  },
  watch: {
    show(value) {
      if (value) this.pin = ''
    }
  },
  computed: {
    errorText() {
      if (!this.error) return ''
      if (!this.attemptsLeft) return this.error
      const word = this.attemptsLeft === 1 ? 'attempt' : 'attempts'
      return `${this.error} (${this.attemptsLeft} ${word} left)`
    }
  },
  methods: {
    press(digit) {
      if (this.loading || this.pin.length >= 4) return
      this.pin += digit
    },
    back() {
      if (this.loading) return
      this.pin = this.pin.slice(0, -1)
    },
    submit() {
      if (this.loading || this.pin.length !== 4) return
      this.$emit('submit', this.pin)
      this.pin = ''
    }
  },
  template: `
    <q-dialog :model-value="show" persistent position="top">
      <q-card class="q-pa-lg q-pt-xl lnbits__dialog-card" style="min-width: 300px">
        <div class="text-center">
          <div class="text-h6">Enter card PIN</div>
          <div class="text-h5 q-my-md" v-text="amount"></div>
          <div class="row justify-center q-gutter-sm q-my-md">
            <q-icon
              v-for="i in 4"
              :key="i"
              :name="pin.length >= i ? 'circle' : 'radio_button_unchecked'"
              size="24px"
            ></q-icon>
          </div>
          <div class="text-negative" style="min-height: 1.5em">
            <span v-if="error" v-text="errorText"></span>
          </div>
        </div>
        <div class="row q-col-gutter-sm q-mt-sm">
          <div v-for="key in keys" :key="key" class="col-4">
            <q-btn
              unelevated
              size="lg"
              class="full-width"
              color="primary"
              :disable="loading"
              :label="key"
              @click="press(key)"
            ></q-btn>
          </div>
          <div class="col-4">
            <q-btn
              unelevated
              size="lg"
              class="full-width"
              color="grey"
              icon="backspace"
              :disable="loading"
              @click="back"
            ></q-btn>
          </div>
          <div class="col-4">
            <q-btn
              unelevated
              size="lg"
              class="full-width"
              color="primary"
              :disable="loading"
              label="0"
              @click="press('0')"
            ></q-btn>
          </div>
          <div class="col-4">
            <q-btn
              unelevated
              size="lg"
              class="full-width"
              color="positive"
              icon="check"
              :loading="loading"
              :disable="pin.length !== 4"
              @click="submit"
            ></q-btn>
          </div>
        </div>
        <div class="row q-mt-md">
          <q-btn flat color="grey" class="q-ml-auto" :disable="loading" @click="$emit('cancel')">Cancel</q-btn>
        </div>
      </q-card>
    </q-dialog>
  `
})
