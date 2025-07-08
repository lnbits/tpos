window.app = Vue.createApp({
  el: '#vue',
  mixins: [window.windowMixin],
  delimiters: ['${', '}'],
  data() {
    return {
      tposId: null,
      currency: null,
      fiatProvider: null,
      payInFiat: false,
      atmPremium: tpos.withdraw_premium / 100,
      withdrawMaximum: 0,
      withdrawPinOpen: null,
      tip_options: null,
      exchangeRateLoaded: false,
      exchangeRate: 1,
      stack: [],
      tipAmount: 0.0,
      tipRounding: null,
      hasNFC: false,
      atmBox: false,
      atmPin: null,
      hidePin: true,
      atmMode: false,
      atmToken: '',
      nfcTagReading: false,
      lnaddressDialog: {
        show: false,
        lnaddress: ''
      },
      lastPaymentsDialog: {
        show: false,
        data: []
      },
      heldCartsDialog: {
        show: false
      },
      invoiceDialog: {
        show: false,
        data: {},
        dismissMsg: null,
        paymentChecker: null,
        internalMemo: null
      },
      tipDialog: {
        show: false
      },
      urlDialog: {
        show: false
      },
      complete: {
        show: false
      },
      rounding: false,
      isFullScreen: false,
      isGridView: this.$q.screen.gt.sm,
      moreBtn: false,
      total: 0.0,
      cartTax: 0.0,
      itemsTable: {
        filter: '',
        columns: [
          {
            name: 'delete',
            align: 'left',
            label: '',
            field: ''
          },
          {
            name: 'edit',
            align: 'left',
            label: '',
            field: ''
          },

          {
            name: 'id',
            align: 'left',
            label: 'ID',
            field: 'id'
          },
          {
            name: 'title',
            align: 'left',
            label: 'Title',
            field: 'title'
          },
          {
            name: 'price',
            align: 'left',
            label: 'Price',
            field: 'price'
          },
          {
            name: 'disabled',
            align: 'left',
            label: 'Disabled',
            field: 'disabled'
          }
        ],
        pagination: {
          rowsPerPage: 10
        }
      },
      monochrome: this.$q.localStorage.getItem('lnbits.tpos.color') || false,
      showPoS: true,
      cartDrawer: this.$q.screen.gt.md,
      searchTerm: '',
      categoryFilter: '',
      cart: new Map(),
      heldCarts: {},
      denomIsSats: tpos.currency == 'sats',
      amount: 0.0,
      amountFormatted: 0,
      totalFormatted: 0,
      amountWithTipFormatted: 0,
      sat: 0,
      fsat: 0,
      totalfsat: 0,
      addedAmount: 0,
      enablePrint: false,
      receiptData: null,
      currency_choice: false,
      _currencyResolver: null
    }
  },
  watch: {
    stack: {
      handler() {
        if (!this.stack.length) {
          this.amount = 0.0
        } else if (this.currency === 'sats') {
          this.amount = Number(this.stack.join(''))
        } else {
          this.amount =
            this.stack.reduce((acc, dig) => acc * 10 + dig, 0) * 0.01
        }
      },
      immediate: true,
      deep: true
    },
    amount: {
      handler(val) {
        this.amountFormatted = this.formatAmount(val, this.currency)
        this.amountWithTipFormatted = this.formatAmount(
          this.amount + this.tipAmount,
          this.currency
        )
        this.sat = Math.ceil(val * this.exchangeRate)
        this.fsat = LNbits.utils.formatSat(this.sat)
      },
      immediate: true
    },
    total: {
      handler(val) {
        this.totalFormatted = this.formatAmount(val, this.currency)
        this.totalSat = Math.ceil(val * this.exchangeRate)
        this.totalfsat = LNbits.utils.formatSat(
          Math.ceil(val * this.exchangeRate)
        )
      },
      immediate: true
    }
  },
  computed: {
    tipAmountSat() {
      if (!this.exchangeRate) return 0
      return Math.ceil(this.tipAmount * this.exchangeRate)
    },
    tipAmountFormatted() {
      return LNbits.utils.formatSat(this.tipAmountSat)
    },
    roundToSugestion() {
      switch (true) {
        case this.amount > 50:
          toNext = 10
          break
        case this.amount > 6:
          toNext = 5
          break
        case this.amount > 2.5:
          toNext = 1
          break
        default:
          toNext = 0.5
          break
      }

      return Math.ceil(this.amount / toNext) * toNext
    },
    fullScreenIcon() {
      return this.isFullScreen ? 'fullscreen_exit' : 'fullscreen'
    },
    filteredItems() {
      // filter out disabled items
      let items = this.items.filter(item => !item.disabled)
      // if searchTerm entered, filter out items that don't match
      if (this.searchTerm) {
        items = items.filter(item => {
          return item.title
            .toLowerCase()
            .includes(this.searchTerm.toLowerCase())
        })
      }
      // if categoryFilter entered, filter out items that don't match
      if (this.categoryFilter) {
        items = items.filter(item => {
          return item.categories
            .map(c => c.toLowerCase())
            .includes(this.categoryFilter.toLowerCase())
        })
      }
      return items
    },
    drawerWidth() {
      return this.$q.screen.lt.sm ? 360 : 450
    },
    drawerItemsHeight() {
      return `overflow-y: auto; height: ${this.$q.screen.gt.sm ? 'calc(100vh - 400px)' : 'calc(100vh - 465px)'}`
    },
    formattedCartTax() {
      return this.formatAmount(this.cartTax, this.currency)
    },
    taxSubtotal() {
      if (this.taxInclusive) return this.totalFormatted
      return this.formatAmount(
        Math.floor(this.total - this.cartTax),
        this.currency
      )
    },
    keypadDisabled() {
      return !this.exchangeRate
    }
  },
  methods: {
    addAmount() {
      this.addedAmount += this.amount
      this.total = +(this.total + this.amount).toFixed(2)
      this.stack = []
    },
    cancelAddAmount() {
      this.total = +(this.total - this.addedAmount).toFixed(2)
      this.addedAmount = 0
      this.stack = []
    },
    addToCart(item, quantity = 1) {
      if (this.cart.has(item.id)) {
        this.cart.set(item.id, {
          ...this.cart.get(item.id),
          quantity: this.cart.get(item.id).quantity + quantity
        })
      } else {
        this.cart.set(item.id, {
          ...item,
          quantity: quantity
        })
      }
      this.total = this.total + this.calculateItemPrice(item, quantity)
      this.cartTaxTotal()
    },
    removeFromCart(item, quantity = 1) {
      let item_quantity = this.cart.get(item.id).quantity
      if (item_quantity == 1 || item_quantity == quantity) {
        this.cart.delete(item.id)
      } else {
        this.cart.set(item.id, {
          ...this.cart.get(item.id),
          quantity: this.cart.get(item.id).quantity - quantity
        })
      }
      this.total = this.total - this.calculateItemPrice(item, quantity)
      this.cartTaxTotal()
    },
    calculateItemPrice(item, qty) {
      let tax = item.tax || this.taxDefault
      if (!tax || this.taxInclusive) return item.price * qty

      // add tax to price
      return item.price * (1 + tax * 0.01) * qty
    },
    cartTaxTotal() {
      if (!this.cart.size) return 0.0
      let total = 0.0
      for (let item of this.cart.values()) {
        let tax = item.tax || this.taxDefault
        if (tax > 0) {
          total += item.price * item.quantity * (tax * 0.01)
        }
      }
      this.cartTax = total
    },
    itemCartQty(item_id) {
      if (this.cart.has(item_id)) {
        return this.cart.get(item_id).quantity
      }
      return 0
    },
    clearCart() {
      this.cart.clear()
      this.cartTax = 0.0
      this.total = 0.0
      this.addedAmount = 0.0
      this.cartDrawer = false
    },
    holdCart() {
      if (!this.cart.size) {
        Quasar.Notify.create({
          type: 'warning',
          message: 'Cart is empty. Nothing to hold.'
        })
        return
      }

      const saveCart = note => {
        const cartId = Date.now()
        this.heldCarts[cartId] = {
          cart: Array.from(this.cart.values()),
          note
        }
        localStorage.setItem('lnbits.heldCarts', JSON.stringify(this.heldCarts))

        this.clearCart()

        Quasar.Notify.create({
          type: 'positive',
          message: `Cart held successfully.`
        })
      }

      this.$q
        .dialog({
          title: 'Hold Cart',
          message: 'Add a note to the cart?',
          prompt: {
            model: '',
            type: 'text'
          },
          cancel: true,
          persistent: false
        })
        .onOk(val => {
          saveCart(val || '')
        })
        .onDismiss(() => {
          return
        })
    },
    restoreCart(cartId) {
      this.clearCart()
      if (this.heldCarts[cartId]) {
        const cartData = this.heldCarts[cartId]
        cartData.cart.forEach(item => {
          this.addToCart(item, item.quantity)
        })
        Quasar.Notify.create({
          type: 'positive',
          message: `Cart restored successfully. Note: ${cartData.note}`
        })
      } else {
        Quasar.Notify.create({
          type: 'negative',
          message: 'Cart not found.'
        })
      }
      this.heldCartsDialog.show = false
      this.$q
        .dialog({
          title: 'Cart Restored',
          message: 'Delete cart from held carts?',
          cancel: true,
          persistent: false
        })
        .onOk(() => {
          this.deleteHeldCart(cartId)
        })
    },
    deleteHeldCart(cartId) {
      if (this.heldCarts[cartId]) {
        delete this.heldCarts[cartId]
        localStorage.setItem('lnbits.heldCarts', JSON.stringify(this.heldCarts))
        Quasar.Notify.create({
          type: 'positive',
          message: `Cart deleted successfully.`
        })
      } else {
        Quasar.Notify.create({
          type: 'negative',
          message: 'Cart not found.'
        })
      }
      if (Object.entries(this.heldCarts).length === 0) {
        this.heldCartsDialog.show = false
      }
    },
    exitAtmMode() {
      this.atmMode = false
      this.getRates()
      this.cancelAddAmount()
    },
    startAtmMode() {
      if (!this.showPoS) {
        this.showPoS = true
      }
      this.clearCart()
      this.cancelAddAmount()

      if (this.atmPremium > 0) {
        this.exchangeRate = this.exchangeRate / (1 + this.atmPremium)
      }
      if (this.withdrawPinOpen != 0) {
        this.atmPin = this.withdrawPinOpen
        this.atmSubmit()
        return
      }
      if (this.withdrawMaximum > 0) {
        this.atmBox = true
      }
    },
    atmSubmit() {
      LNbits.api
        .request('GET', `/tpos/api/v1/atm/${this.tposId}/${this.atmPin}`)
        .then(res => {
          this.atmToken = res.data.id
          if (res.data.claimed == false) {
            this.atmBox = false
            this.atmMode = true
          }
        })
        .catch(LNbits.utils.notifyApiError)
    },
    lnaddressSubmit() {
      LNbits.api
        .request(
          'GET',
          `/tpos/api/v1/tposs/lnaddresscheck?lnaddress=${encodeURIComponent(this.lnaddressDialog.lnaddress)}`,
          null
        )
        .then(response => {
          if (response.data) {
            this.$q.localStorage.set(
              'tpos.lnaddress',
              this.lnaddressDialog.lnaddress
            )
            this.lnaddressDialog.show = false
            this.lnaddress = true
          }
        })
        .catch(error => {
          const errorMessage =
            error.response?.data?.detail || 'An unknown error occurred.'
          LNbits.utils.notifyApiError(errorMessage)
        })
    },
    atmGetWithdraw() {
      const dialog = this.invoiceDialog
      if (this.sat > this.withdrawMaximum) {
        Quasar.Notify.create({
          type: 'negative',
          message: 'Amount exceeds the maximum withdrawal limit.'
        })
        return
      }
      LNbits.api
        .request(
          'GET',
          `/tpos/api/v1/atm/withdraw/` + this.atmToken + `/` + this.sat
        )
        .then(res => {
          if (res.data.status == 'ERROR') {
            Quasar.Notify.create({
              type: 'negative',
              message: res.data.reason
            })
            return
          }
          lnurl = res.data.lnurl
          dialog.data = {payment_request: lnurl}
          dialog.show = true
          this.readNfcTag()
          dialog.dismissMsg = Quasar.Notify.create({
            timeout: 0,
            message: 'Withdraw...'
          })
          if (location.protocol !== 'http:') {
            this.withdrawUrl =
              'wss://' +
              document.domain +
              ':' +
              location.port +
              '/api/v1/ws/' +
              this.atmToken
          } else {
            this.withdrawUrl =
              'ws://' +
              document.domain +
              ':' +
              location.port +
              '/api/v1/ws/' +
              this.atmToken
          }
          this.connectionWithdraw = new WebSocket(this.withdrawUrl)
          this.connectionWithdraw.onmessage = e => {
            if (e.data == 'paid') {
              dialog.show = false
              this.atmPin = null
              this.atmToken = ''
              this.showComplete()
              this.atmMode = false
              this.connectionWithdraw.close()
            }
          }
          this.getRates()
        })
        .catch(LNbits.utils.notifyApiError)
    },
    setRounding() {
      this.rounding = true
      this.tipRounding = this.roundToSugestion
      this.$nextTick(() => this.$refs.inputRounding.focus())
    },
    calculatePercent() {
      const change = ((this.tipRounding - this.amount) / this.amount) * 100
      if (change < 0) {
        Quasar.Notify.create({
          type: 'warning',
          message: 'Amount with tip must be greater than initial amount.'
        })
        this.tipRounding = this.roundToSugestion
        return
      }
      this.processTipSelection(change)
    },
    closeInvoiceDialog() {
      this.stack = []
      this.tipAmount = 0.0
      const dialog = this.invoiceDialog
      setTimeout(() => {
        clearInterval(dialog.paymentChecker)
        dialog.dismissMsg()
      }, 3000)
    },
    processTipSelection(selectedTipOption) {
      this.tipDialog.show = false
      if (!selectedTipOption) {
        this.tipAmount = 0.0
        return this.showInvoice()
      }

      this.tipAmount = (selectedTipOption / 100) * this.amount
      this.showInvoice()
    },
    submitForm() {
      if (this.total != 0.0) {
        if (this.amount > 0.0) {
          this.total += this.amount
        }
        if (this.currency == 'sats') {
          this.stack = Array.from(String(Math.ceil(this.total), Number))
        } else {
          this.stack = Array.from(String(Math.ceil(this.total * 100)), Number)
        }
        this.sat = this.totalSat
      }

      if (!this.exchangeRate || this.exchangeRate == 0 || this.sat == 0) {
        Quasar.Notify.create({
          type: 'negative',
          message:
            'Exchange rate not available, or wrong value. Please try again later.'
        })
        return
      }

      if (this.tip_options && this.tip_options.length) {
        this.rounding = false
        this.tipRounding = null
        this.showTipModal()
      } else {
        this.showInvoice()
      }
    },
    showTipModal() {
      if (!this.atmMode) {
        this.tipDialog.show = true
      } else {
        this.showInvoice()
      }
    },
    showPaymentMethod() {
      return new Promise(resolve => {
        this.currency_choice = true
        this._currencyResolver = resolve
      })
    },
    selectPaymentMethod(method) {
      this.currency_choice = false
      if (this._currencyResolver) {
        this._currencyResolver(method)
        this._currencyResolver = null
      }
    },
    buildInvoiceParams() {
      const params = {
        amount: this.sat,
        memo: this.total > 0 ? this.totalFormatted : this.amountFormatted,
        exchange_rate: this.exchangeRate,
        pay_in_fiat: this.payInFiat
      }
      if (this.currency != LNBITS_DENOMINATION) {
        params.amount_fiat = this.total > 0 ? this.total : this.amount
        params.tip_amount_fiat = this.tipAmount > 0 ? this.tipAmount : 0.0
      }
      if (this.tipAmountSat > 0) {
        params.tip_amount = this.tipAmountSat
      }
      if (this.cart.size) {
        params.details = {
          currency: this.currency,
          exchangeRate: this.exchangeRate,
          items: [...this.cart.values()].map(item => ({
            price: item.price,
            formattedPrice: item.formattedPrice,
            quantity: item.quantity,
            title: item.title,
            tax: item.tax || this.taxDefault
          })),
          taxIncluded: this.taxInclusive,
          taxValue: this.cartTax
        }
      }
      if (this.lnaddress) {
        params.user_lnaddress = this.lnaddressDialog.lnaddress
      }
      return params
    },
    async showInvoice() {
      if (this.atmMode) {
        this.atmGetWithdraw()
        return
      }

      if (this.fiatProvider) {
        const method = await this.showPaymentMethod()
        this.payInFiat = method === 'fiat'
      }

      const params = this.buildInvoiceParams()
      try {
        const {data} = await LNbits.api.request(
          'POST',
          `/tpos/api/v1/tposs/${this.tposId}/invoices`,
          null,
          params
        )
        if (data.extra.fiat_payment_request) {
          this.invoiceDialog.data.payment_request =
            data.extra.fiat_payment_request
        } else {
          this.invoiceDialog.data.payment_request =
            'lightning:' + data.bolt11.toUpperCase()
        }
        this.invoiceDialog.data.payment_hash = data.payment_hash
        this.invoiceDialog.show = true
        this.readNfcTag()
        this.invoiceDialog.dismissMsg = Quasar.Notify.create({
          timeout: 0,
          message: 'Waiting for payment...'
        })
        this.subscribeToPaymentWS(data.payment_hash)
      } catch (error) {
        console.error(error)
        LNbits.utils.notifyApiError(error)
      }
    },
    subscribeToPaymentWS(paymentHash) {
      try {
        const url = new URL(window.location)
        url.protocol = url.protocol === 'https:' ? 'wss' : 'ws'
        url.pathname = `/api/v1/ws/${paymentHash}`
        const ws = new WebSocket(url)
        ws.onmessage = async ({data}) => {
          const payment = JSON.parse(data)
          if (payment.pending === false) {
            Quasar.Notify.create({
              type: 'positive',
              message: 'Invoice Paid!'
            })
            this.invoiceDialog.show = false
            this.invoiceDialog.internalMemo = null
            this.clearCart()
            this.showComplete()
            if (this.enablePrint) {
              this.printReceipt(paymentHash)
            }
            ws.close()
          }
        }
      } catch (err) {
        console.warn(err)
        LNbits.utils.notifyApiError(err)
      }
    },
    readNfcTag() {
      try {
        if (typeof NDEFReader == 'undefined') {
          console.debug('NFC not supported on this device or browser.')
          return
        }

        const ndef = new NDEFReader()

        const readerAbortController = new AbortController()
        readerAbortController.signal.onabort = event => {
          console.debug('All NFC Read operations have been aborted.')
        }

        this.nfcTagReading = true
        Quasar.Notify.create({
          message: this.atmMode
            ? 'Tap your NFC tag to withdraw with LNURLp.'
            : 'Tap your NFC tag to pay this invoice with LNURLw.'
        })

        return ndef.scan({signal: readerAbortController.signal}).then(() => {
          ndef.onreadingerror = event => {
            this.nfcTagReading = false

            Quasar.Notify.create({
              type: 'negative',
              message: 'There was an error reading this NFC tag.',
              caption: event.message || 'Please try again.'
            })

            readerAbortController.abort()
          }

          ndef.onreading = ({message}) => {
            // Abort scan immediately to prevent duplicate reads
            readerAbortController.abort()

            this.nfcTagReading = false

            //Decode NDEF data from tag
            const textDecoder = new TextDecoder('utf-8')

            const record = message.records.find(el => {
              const payload = textDecoder.decode(el.data)
              return payload.toUpperCase().indexOf('LNURL') !== -1
            })

            if (!record) {
              Quasar.Notify.create({
                type: 'negative',
                message: 'No valid LNURL found on this NFC tag.'
              })
              return
            }

            const lnurl = textDecoder.decode(record.data)

            //User feedback, show loader icon
            if (this.atmMode) {
              const URL = lnurl.replace('lnurlp://', 'https://')
              LNbits.api
                .request('GET', `${lnurl.replace('lnurlw://', 'https://')}`)
                .then(res => {
                  this.makeWithdraw(res.data.payLink)
                })
            } else {
              this.payInvoice(lnurl)
            }

            Quasar.Notify.create({
              type: 'positive',
              message: 'NFC tag read successfully.'
            })
          }
        })
      } catch (error) {
        this.nfcTagReading = false
        Quasar.Notify.create({
          type: 'negative',
          message: error
            ? error.toString()
            : 'An unexpected error has occurred.'
        })
      }
    },
    makeWithdraw(payLink) {
      if (!payLink) {
        Quasar.Notify.create({
          type: 'negative',
          message: 'LNURL not found in NFC tag.'
        })
        return
      }
      LNbits.api
        .request(
          'POST',
          `/tpos/api/v1/atm/withdraw/${this.atmToken}/${this.sat}/pay`,
          null,
          {
            pay_link: payLink
          }
        )
        .then(res => {
          if (!res.data.success) {
            Quasar.Notify.create({
              type: 'negative',
              message: res.data.detail
            })
          } else {
            this.stack = []
            this.total = 0.0
            Quasar.Notify.create({
              type: 'positive',
              message: 'Topup successful!'
            })
          }
        })
        .catch(e => {
          console.error(e)
        })
    },
    payInvoice(lnurl) {
      return axios
        .post(
          '/tpos/api/v1/tposs/' +
            this.tposId +
            '/invoices/' +
            this.invoiceDialog.data.payment_request +
            '/pay',
          {
            lnurl: lnurl
          }
        )
        .then(response => {
          if (!response.data.success) {
            Quasar.Notify.create({
              type: 'negative',
              message: response.data.detail
            })
          }
        })
        .catch(error => {
          console.error(error)
        })
    },
    async getRates() {
      let rate = 1
      try {
        if (this.currency != LNBITS_DENOMINATION) {
          const {data} = await LNbits.api.request(
            'GET',
            `/api/v1/rate/${this.currency}`
          )
          rate = data.rate
        }
      } catch (e) {
        console.error(e)
      } finally {
        Quasar.Loading.hide()
        this.exchangeRate = rate
        return rate
      }
    },
    getLastPayments() {
      return axios
        .get(`/tpos/api/v1/tposs/${this.tposId}/invoices`)
        .then(res => {
          if (res.data && res.data.length) {
            let last = [...res.data]
            this.lastPaymentsDialog.data = last.map(obj => {
              obj.dateFrom = moment(obj.time).fromNow()
              if (obj.currency != LNBITS_DENOMINATION) {
                obj.amountFiat = this.formatAmount(
                  obj.amount / 1000 / this.exchangeRate,
                  this.currency
                )
              }
              return obj
            })
          }
        })
        .catch(e => console.error(e))
    },
    showLastPayments() {
      this.getLastPayments()
      this.lastPaymentsDialog.show = true
    },
    toggleFullscreen() {
      if (document.fullscreenElement) return document.exitFullscreen()

      const elem = document.documentElement
      elem
        .requestFullscreen({navigationUI: 'show'})
        .then(() => {
          document.addEventListener('fullscreenchange', () => {
            this.isFullScreen = document.fullscreenElement
          })
        })
        .catch(err => console.error(err))
    },
    handleColorScheme(val) {
      this.$q.localStorage.set('lnbits.tpos.color', val)
    },
    clearLNaddress() {
      this.$q.localStorage.remove('tpos.lnaddress')
      this.lnaddressDialog.lnaddress = ''
      const url = new URL(window.location.href)
      url.searchParams.delete('lnaddress')
      window.history.replaceState({}, document.title, url.toString())
      this.lnaddressDialog.show = true
      this.lnaddress = false
    },
    extractCategories(items) {
      let categories = new Set()
      items
        .map(item => {
          item.categories &&
            item.categories.forEach(category => categories.add(category))
        })
        .filter(Boolean)

      if (categories.size) {
        categories = ['All', ...categories]
      }
      return Array.from(categories)
    },
    handleCategoryBtn(category) {
      if (this.categoryFilter == category) {
        this.categoryFilter = ''
      } else {
        this.categoryFilter = category == 'All' ? '' : category
      }
    },
    formatAmount(amount, currency) {
      if (LNBITS_DENOMINATION != 'sats') {
        return LNbits.utils.formatCurrency(amount / 100, LNBITS_DENOMINATION)
      }
      if (currency == 'sats') {
        return LNbits.utils.formatSat(amount) + ' sats'
      } else {
        return LNbits.utils.formatCurrency(Number(amount).toFixed(2), currency)
      }
    },
    formatDate(timestamp) {
      return LNbits.utils.formatDate(timestamp / 1000)
    },
    showComplete() {
      this.complete.show = true
      if (this.$q.screen.lt.lg && this.cartDrawer) {
        this.cartDrawer = false
      }
    },
    async printReceipt(paymentHash) {
      this.receiptData = null
      try {
        const {data} = await LNbits.api.request(
          'GET',
          `/tpos/api/v1/tposs/${this.tposId}/invoices/${paymentHash}?extra=true`
        )
        this.receiptData = data

        this.$q
          .dialog({
            title: 'Print Receipt',
            message: 'Do you want to print the receipt?',
            cancel: true,
            persistent: false
          })
          .onOk(() => {
            console.log('Printing receipt for payment hash:', paymentHash)
            window.print()
          })
      } catch (error) {
        console.error('Error fetching receipt data:', error)
        Quasar.Notify.create({
          type: 'negative',
          message: 'Error fetching receipt data.'
        })
      }
    },
    async addComment() {
      this.$q
        .dialog({
          title: 'Add Comment',
          message: 'Add a comment to the invoice?',
          prompt: {
            model: '',
            type: 'text',
            placeholder: 'Enter your comment here...'
          },
          cancel: true,
          persistent: false
        })
        .onOk(comment => {
          if (comment) {
            this.invoiceDialog.internalMemo = comment
            Quasar.Notify.create({
              type: 'positive',
              message: 'Comment added to the invoice.'
            })
          }
        })
        .onCancel(() => {
          this.invoiceDialog.internalMemo = ''
        })
    }
  },
  async created() {
    Quasar.Loading.show()
    this.currency = tpos.currency
    this.amountFormatted = this.formatAmount(this.amount, this.currency)
    this.totalFormatted = this.formatAmount(this.total, this.currency)
    this.tposId = tpos.id
    this.atmPremium = tpos.withdraw_premium / 100
    this.withdrawMaximum = withdraw_maximum
    this.withdrawPinOpen = withdraw_pin_open
    this.pinDisabled = tpos.withdraw_pin_disabled
    this.taxInclusive = tpos.tax_inclusive
    this.taxDefault = tpos.tax_default
    this.tposLNaddress = tpos.lnaddress
    this.tposLNaddressCut = tpos.lnaddress_cut
    this.enablePrint = tpos.enable_receipt_print
    this.fiatProvider = tpos.fiat_provider

    this.tip_options = tpos.tip_options == 'null' ? null : tpos.tip_options

    if (tpos.tip_wallet) {
      this.tip_options.push('Round')
    }

    this.items = tpos.items
    this.items.forEach((item, id) => {
      item.formattedPrice = this.formatAmount(item.price, this.currency)
      item.id = id
      return item
    })
    if (this.items.length > 0) {
      this.showPoS = false
      this.categories = this.extractCategories(this.items)
    }
    this.exchangeRate = await this.getRates()
    this.heldCarts = JSON.parse(
      localStorage.getItem('lnbits.heldCarts') || '{}'
    )
    // remove held carts older than 1 day
    const now = Date.now()
    Object.keys(this.heldCarts).forEach(cartId => {
      if (cartId < now - 86400000) {
        delete this.heldCarts[cartId]
      }
    })
  },
  onMounted() {
    setInterval(() => {
      this.getRates()
    }, 120000)
    if (this.tposLNaddress) {
      this.lnaddressDialog.lnaddress =
        this.$q.localStorage.getItem('tpos.lnaddress')
      if (lnaddressparam != '') {
        this.lnaddressDialog.lnaddress = lnaddressparam
        this.$q.localStorage.set(
          'tpos.lnaddress',
          this.lnaddressDialog.lnaddress
        )
        this.lnaddress = true
      } else if (!this.lnaddressDialog.lnaddress) {
        this.lnaddress = false
        this.lnaddressDialog.show = true
      } else {
        this.lnaddress = true
      }
    }

    window.addEventListener('keyup', event => {
      // do nothing if the event was already processed
      if (event.defaultPrevented) return

      // active only in the the PoS mode, not in the Cart mode or ATM pin
      if (!this.showPoS || this.atmBox) return

      // prevent weird behaviour when setting round tip
      if (this.tipDialog.show) return

      const {key} = event
      if (key >= '0' && key <= '9') {
        // buttons 0 ... 9
        this.stack.push(Number(key))
      } else
        switch (key) {
          case 'Backspace': // button ⬅
            this.stack.pop()
            break
          case 'Enter': // button OK
            this.submitForm()
            break
          case 'c': // button C
          case 'C':
          case 'Escape':
            if (this.total > 0.0) {
              this.cancelAddAmount()
            } else {
              this.stack = []
            }
            break
          case '+': // button +
            this.addAmount()
            break
          default:
            // return if we didn't handle anything
            return
        }

      // cancel the default action to avoid it being handled twice
      event.preventDefault()
    })
  }
})
