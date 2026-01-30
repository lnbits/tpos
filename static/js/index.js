const mapTpos = obj => {
  obj.date = Quasar.date.formatDate(
    new Date(obj.time * 1000),
    'YYYY-MM-DD HH:mm'
  )
  obj.tpos = ['/tpos/', obj.id].join('')
  obj.shareUrl = [
    window.location.protocol,
    '//',
    window.location.host,
    obj.tpos
  ].join('')
  obj.items = obj.items ? JSON.parse(obj.items) : []
  obj.use_inventory = Boolean(obj.use_inventory)
  obj.inventory_id = obj.inventory_id || null
  const tagString =
    obj.inventory_tags === 'null' ? '' : obj.inventory_tags || ''
  obj.inventory_tags = tagString ? tagString.split(',').filter(Boolean) : []
  const omitTagString =
    obj.inventory_omit_tags === 'null' ? '' : obj.inventory_omit_tags || ''
  obj.inventory_omit_tags = omitTagString
    ? omitTagString.split(',').filter(Boolean)
    : []
  obj.only_show_sats_on_bitcoin = obj.only_show_sats_on_bitcoin ?? true
  obj.itemsMap = new Map()
  obj.items.forEach((item, idx) => {
    let id = `${obj.id}:${idx + 1}`
    obj.itemsMap.set(id, {...item, id})
  })
  return obj
}

window.app = Vue.createApp({
  el: '#vue',
  mixins: [window.windowMixin],
  data() {
    return {
      tposs: [],
      currencyOptions: [],
      hasFiatProvider: false,
      fiatProviders: null,
      inventoryStatus: {
        enabled: false,
        inventory_id: null,
        tags: [],
        omit_tags: []
      },
      tpossTable: {
        columns: [
          {name: 'name', align: 'left', label: 'Name', field: 'name'},
          {
            name: 'currency',
            align: 'left',
            label: 'Currency',
            field: 'currency'
          },
          {
            name: 'fiat_provider',
            align: 'left',
            label: 'Fiat Provider',
            field: 'fiat_provider',
            format: val => val && val.charAt(0).toUpperCase() + val.slice(1)
          },
          {
            name: 'withdraw_time_option',
            align: 'left',
            label: 'mins/sec',
            field: 'withdraw_time_option'
          },
          {
            name: 'lnaddress',
            align: 'left',
            label: 'LNaddress',
            field: 'lnaddress'
          },
          {
            name: 'lnaddress_cut',
            align: 'left',
            label: 'LNaddress Cut',
            field: 'lnaddress_cut'
          }
        ],
        pagination: {
          rowsPerPage: 10
        }
      },
      withdraw_options: [
        {
          label: 'Mins',
          value: 'mins'
        },
        {
          label: 'Secs',
          value: 'secs'
        }
      ],
      formDialog: {
        show: false,
        data: {
          use_inventory: false,
          inventory_id: null,
          inventory_tags: [],
          tip_options: [],
          withdraw_between: 10,
          withdraw_time_option: '',
          tax_inclusive: true,
          lnaddress: false,
          lnaddress_cut: 2,
          enable_receipt_print: false,
          only_show_sats_on_bitcoin: true,
          fiat: false,
          stripe_card_payments: false,
          stripe_reader_id: ''
        },
        advanced: {
          tips: false,
          otc: false
        }
      },
      itemDialog: {
        show: false,
        data: {
          title: '',
          image: '',
          price: '',
          categories: [],
          disabled: false
        }
      },
      tab: 'items',
      itemsTable: {
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
            name: 'tax',
            align: 'left',
            label: 'Tax',
            field: 'tax'
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
      fileDataDialog: {
        show: false,
        data: {},
        import: () => {}
      },
      urlDialog: {
        show: false,
        data: {}
      }
    }
  },
  computed: {
    anyRecordsWithFiat() {
      return this.tposs.some(t => !!t.stripe_card_payments)
    },
    categoryList() {
      return Array.from(
        new Set(
          this.tposs.flatMap(tpos =>
            tpos.items.flatMap(item => item.categories || [])
          )
        )
      )
    },
    createOrUpdateDisabled() {
      if (!this.formDialog.show) return true
      const data = this.formDialog.data
      return (
        !data.name ||
        !data.currency ||
        !data.wallet ||
        (this.formDialog.advanced.otc && !data.withdraw_limit)
      )
    },
    inventoryModeOptions() {
      return [
        {
          label: 'Use inventory extension',
          value: true,
          disable: !this.inventoryStatus.enabled
        },
        {label: 'Use TPoS items', value: false}
      ]
    },
    inventoryTagOptions() {
      return (this.inventoryStatus.tags || []).map(tag => ({
        label: tag,
        value: tag
      }))
    },
    inventoryOmitTagOptions() {
      return (this.inventoryStatus.omit_tags || []).map(tag => ({
        label: tag,
        value: tag
      }))
    }
  },
  methods: {
    closeFormDialog() {
      this.formDialog.show = false
      this.formDialog.data = {
        use_inventory: false,
        inventory_id: this.inventoryStatus.inventory_id,
        inventory_tags: [...(this.inventoryStatus.tags || [])],
        inventory_omit_tags: [...(this.inventoryStatus.omit_tags || [])],
        tip_options: [],
        withdraw_between: 10,
        withdraw_time_option: '',
        tax_inclusive: true,
        lnaddress: false,
        lnaddress_cut: 2,
        enable_receipt_print: false,
        only_show_sats_on_bitcoin: true,
        fiat: false,
        stripe_card_payments: false,
        stripe_reader_id: ''
      }
      this.formDialog.advanced = {tips: false, otc: false}
    },
    getTposs() {
      LNbits.api
        .request(
          'GET',
          '/tpos/api/v1/tposs?all_wallets=true',
          this.g.user.wallets[0].inkey
        )
        .then(response => {
          this.tposs = response.data.map(obj => {
            return mapTpos(obj)
          })
        })
    },
    async loadInventoryStatus() {
      if (!this.g.user.wallets.length) return
      try {
        const {data} = await LNbits.api.request(
          'GET',
          '/tpos/api/v1/inventory/status',
          this.g.user.wallets[0].adminkey
        )
        this.inventoryStatus = data
        // Default remains "Use TPoS items"; keep inventory info available without auto-enabling.
        if (!this.formDialog.data.inventory_id) {
          this.formDialog.data.inventory_id = data.inventory_id
          this.formDialog.data.inventory_tags = [...data.tags]
          this.formDialog.data.inventory_omit_tags = [...data.omit_tags]
        }
      } catch (error) {
        console.error(error)
      }
    },
    sendTposData() {
      const data = {
        ...this.formDialog.data,
        tip_options:
          this.formDialog.advanced.tips && this.formDialog.data.tip_options
            ? JSON.stringify(
                this.formDialog.data.tip_options.map(str => parseInt(str))
              )
            : JSON.stringify([]),
        tip_wallet:
          (this.formDialog.advanced.tips && this.formDialog.data.tip_wallet) ||
          '',
        items: JSON.stringify(this.formDialog.data.items || [])
      }
      data.inventory_tags = data.inventory_tags || []
      if (!this.inventoryStatus.enabled) {
        data.use_inventory = false
      } else if (!data.inventory_id) {
        data.inventory_id = this.inventoryStatus.inventory_id
      }
      if (data.use_inventory && !data.inventory_id) {
        data.use_inventory = false
      }
      // delete withdraw_between if value is empty string, defaults to 10 minutes
      if (this.formDialog.data.withdraw_between == '') {
        delete data.withdraw_between
      }
      if (!this.formDialog.advanced.otc) {
        data.withdraw_limit = null
        data.withdraw_premium = null
      }
      const wallet = _.findWhere(this.g.user.wallets, {
        id: this.formDialog.data.wallet
      })
      if (data.id) {
        this.updateTpos(wallet, data)
      } else {
        this.createTpos(wallet, data)
      }
    },
    updateTposForm(tposId) {
      const tpos = _.findWhere(this.tposs, {id: tposId})
      this.formDialog.data = {
        ...tpos,
        tip_options: JSON.parse(tpos.tip_options)
      }
      if (this.formDialog.data.tip_wallet != '') {
        this.formDialog.advanced.tips = true
      }
      if (this.formDialog.data.withdraw_limit >= 1) {
        this.formDialog.advanced.otc = true
      } else {
        this.formDialog.advanced.otc = false
      }
      this.formDialog.show = true
    },
    createTpos(wallet, data) {
      LNbits.api
        .request('POST', '/tpos/api/v1/tposs', wallet.adminkey, data)
        .then(response => {
          this.tposs.push(mapTpos(response.data))
          this.closeFormDialog()
        })
        .catch(error => {
          LNbits.utils.notifyApiError(error)
        })
    },
    updateTpos(wallet, data) {
      LNbits.api
        .request('PUT', `/tpos/api/v1/tposs/${data.id}`, wallet.adminkey, data)
        .then(response => {
          this.tposs = _.reject(this.tposs, obj => {
            return obj.id == data.id
          })
          this.tposs.push(mapTpos(response.data))
          this.closeFormDialog()
        })
        .catch(error => {
          LNbits.utils.notifyApiError(error)
        })
    },
    saveInventorySettings(tpos) {
      const wallet = _.findWhere(this.g.user.wallets, {id: tpos.wallet})
      if (!wallet) return
      const resolvedInventoryId =
        this.inventoryStatus.inventory_id || tpos.inventory_id
      const payload = {
        use_inventory: this.inventoryStatus.enabled && tpos.use_inventory,
        inventory_id:
          this.inventoryStatus.enabled && tpos.use_inventory
            ? resolvedInventoryId
            : null,
        inventory_tags: tpos.inventory_tags || [],
        inventory_omit_tags: tpos.inventory_omit_tags || []
      }
      if (payload.use_inventory && !payload.inventory_id) {
        Quasar.Notify.create({
          type: 'warning',
          message: 'No inventory found for this user.'
        })
        return
      }
      LNbits.api
        .request(
          'PUT',
          `/tpos/api/v1/tposs/${tpos.id}`,
          wallet.adminkey,
          payload
        )
        .then(response => {
          this.tposs = _.reject(this.tposs, obj => obj.id == tpos.id)
          this.tposs.push(mapTpos(response.data))
        })
        .catch(LNbits.utils.notifyApiError)
    },
    onInventoryModeChange(tpos, value) {
      tpos.use_inventory = value
      if (value && this.inventoryStatus.enabled) {
        tpos.inventory_id = this.inventoryStatus.inventory_id
        if (!tpos.inventory_tags.length && this.inventoryStatus.tags.length) {
          tpos.inventory_tags = [...this.inventoryStatus.tags]
        }
        if (
          !tpos.inventory_omit_tags.length &&
          this.inventoryStatus.omit_tags
        ) {
          tpos.inventory_omit_tags = [...this.inventoryStatus.omit_tags]
        }
      }
      this.saveInventorySettings(tpos)
    },
    onInventoryTagsChange(tpos, tags) {
      tpos.inventory_tags = tags || []
      this.saveInventorySettings(tpos)
    },
    onInventoryOmitTagsChange(tpos, tags) {
      tpos.inventory_omit_tags = tags || []
      this.saveInventorySettings(tpos)
    },
    deleteTpos(tposId) {
      const tpos = _.findWhere(this.tposs, {id: tposId})

      LNbits.utils
        .confirmDialog('Are you sure you want to delete this Tpos?')
        .onOk(() => {
          LNbits.api
            .request(
              'DELETE',
              '/tpos/api/v1/tposs/' + tposId,
              _.findWhere(this.g.user.wallets, {id: tpos.wallet}).adminkey
            )
            .then(() => {
              this.tposs = _.reject(this.tposs, obj => {
                return obj.id == tposId
              })
            })
            .catch(LNbits.utils.notifyApiError)
        })
    },
    exportCSV() {
      LNbits.utils.exportCSV(this.tpossTable.columns, this.tposs)
    },
    itemsArray(tposId) {
      const tpos = _.findWhere(this.tposs, {id: tposId})
      return [...tpos.itemsMap.values()]
    },
    itemFormatPrice(price, id) {
      const tpos = id.split(':')[0]
      const currency = _.findWhere(this.tposs, {id: tpos}).currency
      if (currency == 'sats') {
        return LNbits.utils.formatSat(price) + ' sat'
      } else {
        return LNbits.utils.formatCurrency(Number(price).toFixed(2), currency)
      }
    },
    openItemDialog(id) {
      const [tposId, itemId] = id.split(':')
      const tpos = _.findWhere(this.tposs, {id: tposId})

      if (itemId) {
        const item = tpos.itemsMap.get(id)
        this.itemDialog.data = {
          ...item,
          tpos: tposId
        }
      } else {
        this.itemDialog.data.tpos = tposId
      }
      this.itemDialog.taxInclusive = tpos.tax_inclusive
      this.itemDialog.data.currency = tpos.currency
      this.itemDialog.show = true
    },
    closeItemDialog() {
      this.itemDialog.show = false
      this.itemDialog.data = {
        title: '',
        image: '',
        price: '',
        categories: [],
        disabled: false
      }
    },
    deleteItem(id) {
      const [tposId, itemId] = id.split(':')
      const tpos = _.findWhere(this.tposs, {id: tposId})
      const wallet = _.findWhere(this.g.user.wallets, {
        id: tpos.wallet
      })
      LNbits.utils
        .confirmDialog('Are you sure you want to delete this item?')
        .onOk(() => {
          tpos.itemsMap.delete(id)
          const data = {
            items: [...tpos.itemsMap.values()]
          }
          this.updateTposItems(tpos.id, wallet, data)
        })
    },
    addItems() {
      const tpos = _.findWhere(this.tposs, {id: this.itemDialog.data.tpos})
      const wallet = _.findWhere(this.g.user.wallets, {
        id: tpos.wallet
      })
      if (this.itemDialog.data.id) {
        tpos.itemsMap.set(this.itemDialog.data.id, this.itemDialog.data)
      }
      const data = {
        items: this.itemDialog.data.id
          ? [...tpos.itemsMap.values()]
          : [...tpos.items, this.itemDialog.data]
      }
      this.updateTposItems(tpos.id, wallet, data)
    },
    deleteAllItems(tposId) {
      const tpos = _.findWhere(this.tposs, {id: tposId})
      const wallet = _.findWhere(this.g.user.wallets, {
        id: tpos.wallet
      })
      LNbits.utils
        .confirmDialog('Are you sure you want to delete ALL items?')
        .onOk(() => {
          tpos.itemsMap.clear()
          const data = {
            items: []
          }
          this.updateTposItems(tpos.id, wallet, data)
        })
    },
    updateTposItems(tposId, wallet, data) {
      const tpos = _.findWhere(this.tposs, {id: tposId})
      if (tpos.tax_inclusive != this.taxInclusive) {
        data.tax_inclusive = this.taxInclusive
      }
      LNbits.api
        .request(
          'PUT',
          `/tpos/api/v1/tposs/${tposId}/items`,
          wallet.adminkey,
          data
        )
        .then(response => {
          this.tposs = _.reject(this.tposs, obj => {
            return obj.id == tposId
          })
          this.tposs.push(mapTpos(response.data))
          this.closeItemDialog()
        })
        .catch(error => {
          LNbits.utils.notifyApiError(error)
        })
    },
    exportJSON(tposId) {
      const tpos = _.findWhere(this.tposs, {id: tposId})
      const data = [...tpos.items]
      const filename = `items_${tpos.id}.json`
      const json = JSON.stringify(data, null, 2)
      let status = Quasar.exportFile(filename, json)
      if (status !== true) {
        Quasar.Notify.create({
          message: 'Browser denied file download...',
          color: 'negative'
        })
      }
    },
    importJSON(tposId) {
      try {
        let input = document.getElementById('import')
        input.click()
        input.onchange = e => {
          let file = e.target.files[0]
          let reader = new FileReader()
          reader.readAsText(file, 'UTF-8')
          reader.onload = async readerEvent => {
            try {
              let content = readerEvent.target.result
              let data = JSON.parse(content).filter(
                obj => obj.title && obj.price
              )
              if (!data.length) {
                throw new Error('Invalid JSON or missing data.')
              }
              this.openFileDataDialog(tposId, data)
            } catch (error) {
              Quasar.Notify.create({
                message: `Error importing file. ${error.message}`,
                color: 'negative'
              })
              return
            }
          }
        }
      } catch (error) {
        Quasar.Notify.create({
          message: 'Error importing file',
          color: 'negative'
        })
      }
    },
    openFileDataDialog(tposId, data) {
      const tpos = _.findWhere(this.tposs, {id: tposId})
      const wallet = _.findWhere(this.g.user.wallets, {
        id: tpos.wallet
      })
      data.forEach(item => {
        item.formattedPrice = this.formatAmount(item.price, tpos.currency)
      })
      this.fileDataDialog.data = data
      this.fileDataDialog.count = data.length
      this.fileDataDialog.show = true
      this.fileDataDialog.import = () => {
        let updatedData = {
          items: [...tpos.items, ...data]
        }
        this.updateTposItems(tpos.id, wallet, updatedData)
        this.fileDataDialog.data = {}
        this.fileDataDialog.show = false
      }
    },
    openUrlDialog(id) {
      if (this.tposs.stripe_card_payments) {
        this.urlDialog.data = _.findWhere(this.tposs, {
          id,
          stripe_card_payments: true
        })
      } else {
        this.urlDialog.data = _.findWhere(this.tposs, {id})
      }
      this.urlDialog.show = true
    },
    formatAmount(amount, currency) {
      if (currency == 'sats') {
        return LNbits.utils.formatSat(amount) + ' sat'
      } else {
        return LNbits.utils.formatCurrency(Number(amount).toFixed(2), currency)
      }
    }
  },
  created() {
    if (this.g.user.wallets.length) {
      this.getTposs()
      this.loadInventoryStatus()
    }
    LNbits.api
      .request('GET', '/api/v1/currencies')
      .then(response => {
        this.currencyOptions = ['sats', ...response.data]
        if (g.settings.denomination != 'sats') {
          this.formDialog.data.currency = g.settings.denomination
        }
      })
      .catch(LNbits.utils.notifyApiError)
    if (this.g.user.fiat_providers && this.g.user.fiat_providers.length > 0) {
      this.hasFiatProvider = true
      this.fiatProviders = [...this.g.user.fiat_providers]
    }
  }
})
