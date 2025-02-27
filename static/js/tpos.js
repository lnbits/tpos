window.app = Vue.createApp({
  el: '#vue',
  mixins: [window.windowMixin],
  delimiters: ['${', '}'],
  data: function () {
    return {
      tposId: null,
      currency: null,
      atmPremium: tpos.withdraw_premium / 100,
      withdrawMaximum: 0,
      withdrawPinOpen: null,
      tip_options: null,
      exchangeRate: null,
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
      invoiceDialog: {
        show: false,
        data: null,
        dismissMsg: null,
        paymentChecker: null
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
      denomIsSats: tpos.currency == 'sats'
    }
  },
  computed: {
    amount() {
      if (!this.stack.length) return 0.0
      if (this.currency == 'sats') return Number(this.stack.join(''))
      return (
        this.stack.reduce((acc, dig) => acc * 10 + dig, 0) *
        (this.currency == 'sats' ? 1 : 0.01)
      )
    },
    amountFormatted() {
      return this.formatAmount(this.amount, this.currency)
    },
    totalFormatted() {
      return this.formatAmount(this.total, this.currency)
    },
    amountWithTipFormatted() {
      return this.formatAmount(this.amount + this.tipAmount, this.currency)
    },
    sat() {
      if (!this.exchangeRate) return 0
      return Math.ceil(this.amount * this.exchangeRate)
    },
    totalSat() {
      if (!this.exchangeRate) return 0
      return Math.ceil(this.total * this.exchangeRate)
    },
    tipAmountSat() {
      if (!this.exchangeRate) return 0
      return Math.ceil(this.tipAmount * this.exchangeRate)
    },
    tipAmountFormatted() {
      return LNbits.utils.formatSat(this.tipAmountSat)
    },
    fsat() {
      return LNbits.utils.formatSat(this.sat)
    },
    totalfsat() {
      return LNbits.utils.formatSat(this.totalSat)
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
      return this.$q.screen.lt.sm ? 375 : 450
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
      this.total = +(this.total + this.amount).toFixed(2)
      this.stack = []
    },
    cancelAddAmount() {
      this.total = 0.0
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
    clearCart() {
      this.cart.clear()
      this.cartTax = 0.0
      this.total = 0.0
    },
    startAtmMode() {
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
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
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
      var dialog = this.invoiceDialog
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
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    setRounding() {
      this.rounding = true
      this.tipRounding = this.roundToSugestion
      this.$nextTick(() => this.$refs.inputRounding.focus())
    },
    calculatePercent() {
      let change = ((this.tipRounding - this.amount) / this.amount) * 100
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
    closeInvoiceDialog: function () {
      this.stack = []
      this.tipAmount = 0.0
      var dialog = this.invoiceDialog
      setTimeout(function () {
        clearInterval(dialog.paymentChecker)
        dialog.dismissMsg()
      }, 3000)
    },
    processTipSelection: function (selectedTipOption) {
      this.tipDialog.show = false
      if (!selectedTipOption) {
        this.tipAmount = 0.0
        return this.showInvoice()
      }

      this.tipAmount = (selectedTipOption / 100) * this.amount
      this.showInvoice()
    },
    submitForm: function () {
      if (this.total != 0.0) {
        if (this.amount > 0.0) {
          this.total = this.total + this.amount
        }
        if (this.currency == 'sats') {
          this.stack = Array.from(String(Math.ceil(this.total), Number))
        } else {
          this.stack = Array.from(String(Math.ceil(this.total * 100)), Number)
        }
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
    showTipModal: function () {
      if (!this.atmMode) {
        this.tipDialog.show = true
      } else {
        this.showInvoice()
      }
    },
    showInvoice() {
      if (this.atmMode) {
        this.atmGetWithdraw()
      } else {
        var dialog = this.invoiceDialog

        let params = {
          amount: this.sat,
          memo: this.amountFormatted
        }
        if (this.tipAmountSat > 0) {
          params.tip_amount = this.tipAmountSat
        }
        if (this.cart.size) {
          let details = [...this.cart.values()].map(item => {
            return {
              price: item.price,
              formattedPrice: item.formattedPrice,
              quantity: item.quantity,
              title: item.title,
              tax: item.tax || this.taxDefault
            }
          })

          params.details = {
            currency: this.currency,
            exchangeRate: this.exchangeRate,
            items: details,
            taxIncluded: this.taxInclusive,
            taxValue: this.cartTax
          }
        }
        if (this.lnaddress) {
          params.user_lnaddress = this.lnaddressDialog.lnaddress
        }
        axios
          .post(`/tpos/api/v1/tposs/${this.tposId}/invoices`, params)
          .then(response => {
            dialog.data = response.data
            dialog.show = true
            this.readNfcTag()

            dialog.dismissMsg = Quasar.Notify.create({
              timeout: 0,
              message: 'Waiting for payment...'
            })

            dialog.paymentChecker = setInterval(() => {
              axios
                .get(
                  `/tpos/api/v1/tposs/${this.tposId}/invoices/${response.data.payment_hash}`
                )
                .then(res => {
                  if (res.data.paid) {
                    clearInterval(dialog.paymentChecker)
                    dialog.dismissMsg()
                    dialog.show = false
                    this.clearCart()

                    this.showComplete()
                  }
                })
            }, 3000)
          })
          .catch(function (error) {
            LNbits.utils.notifyApiError(error)
          })
      }
    },
    readNfcTag: function () {
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
          ndef.onreadingerror = () => {
            this.nfcTagReading = false

            Quasar.Notify.create({
              type: 'negative',
              message: 'There was an error reading this NFC tag.'
            })

            readerAbortController.abort()
          }

          ndef.onreading = ({message}) => {
            //Decode NDEF data from tag
            const textDecoder = new TextDecoder('utf-8')

            const record = message.records.find(el => {
              const payload = textDecoder.decode(el.data)
              return payload.toUpperCase().indexOf('LNURL') !== -1
            })

            const lnurl = textDecoder.decode(record.data)

            //User feedback, show loader icon
            this.nfcTagReading = false
            if (this.atmMode) {
              const URL = lnurl.replace('lnurlp://', 'https://')
              LNbits.api
                .request('GET', `${lnurl.replace('lnurlw://', 'https://')}`)
                .then(res => {
                  this.makeWithdraw(res.data.payLink, readerAbortController)
                })
            } else {
              this.payInvoice(lnurl, readerAbortController)
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
    makeWithdraw(payLink, readerAbortController) {
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
          readerAbortController.abort()
        })
        .catch(e => {
          console.error(e)
          readerAbortController.abort()
        })
    },
    payInvoice(lnurl, readerAbortController) {
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

          readerAbortController.abort()
        })
    },
    getRates() {
      if (this.currency == LNBITS_DENOMINATION) {
        this.exchangeRate = 1
        Quasar.Loading.hide()
      } else {
        LNbits.api
          .request('GET', `/api/v1/rate/${this.currency}`)
          .then(response => {
            this.exchangeRate = response.data.rate
            Quasar.Loading.hide()
          })
          .catch(e => console.error(e))
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
    formatAmount: function (amount, currency) {
      if (LNBITS_DENOMINATION != 'sats') {
        return LNbits.utils.formatCurrency(amount / 100, LNBITS_DENOMINATION)
      }
      if (currency == 'sats') {
        return LNbits.utils.formatSat(amount) + ' sats'
      } else {
        return LNbits.utils.formatCurrency(Number(amount).toFixed(2), currency)
      }
    },
    showComplete() {
      this.complete.show = true
      if (this.$q.screen.lt.lg && this.cartDrawer) {
        this.cartDrawer = false
      }
    }
  },
  created() {
    Quasar.Loading.show()
    this.tposId = tpos.id
    this.currency = tpos.currency
    this.atmPremium = tpos.withdraw_premium / 100
    this.withdrawMaximum = withdraw_maximum
    this.withdrawPinOpen = withdraw_pin_open
    this.getRates()
    setInterval(() => {
      this.getRates()
    }, 120000)
    this.pinDisabled = tpos.withdraw_pin_disabled
    this.taxInclusive = tpos.tax_inclusive
    this.taxDefault = tpos.tax_default
    this.tposLNaddress = tpos.lnaddress
    this.tposLNaddressCut = tpos.lnaddress_cut

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
