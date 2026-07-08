window.tposUtils = {
  getTposCurrencyFractionDigits(currency) {
    const code = (currency || '').toUpperCase()
    if (code === 'SAT' || code === 'SATS') {
      return 0
    }
    try {
      return new Intl.NumberFormat(window.i18n.global.locale, {
        style: 'currency',
        currency: code
      }).resolvedOptions().maximumFractionDigits
    } catch (e) {
      return 2
    }
  },

  getTposCurrencyScale(currency) {
    return 10 ** window.tposUtils.getTposCurrencyFractionDigits(currency)
  },

  roundTposCurrencyAmount(amount, currency) {
    const value = Number(amount) || 0
    if ((currency || '').toLowerCase() === 'sats') {
      return Math.ceil(value)
    }
    const scale = window.tposUtils.getTposCurrencyScale(currency)
    return Math.round(value * scale) / scale
  }
}
