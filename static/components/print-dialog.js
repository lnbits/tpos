window.app.component('tpos-print-dialog', {
  name: 'tpos-print-dialog',
  props: ['show', 'paymentHash'],
  emits: ['update:show', 'close', 'print-order-receipt', 'print-receipt'],
  template: `
    <q-dialog
      :model-value="show"
      persistent
      @update:model-value="$emit('update:show', $event)"
      @hide="$emit('close')"
    >
      <q-card class="q-pa-lg q-pt-xl lnbits__dialog-card">
        <q-card-section class="row items-center q-pb-none">
          <div class="text-h6">Print Receipt</div>
        </q-card-section>
        <q-card-actions align="between">
          <div>
            <q-btn
              round
              icon="receipt_long"
              color="secondary"
              class="q-mr-sm"
              @click.stop="$emit('print-order-receipt', paymentHash)"
            >
              <q-tooltip>Print order receipt</q-tooltip>
            </q-btn>
            <q-btn
              round
              icon="print"
              color="primary"
              @click.stop="$emit('print-receipt', paymentHash)"
            >
              <q-tooltip>Print receipt</q-tooltip>
            </q-btn>
          </div>
          <q-btn outline color="grey" @click="$emit('close')">CLOSE</q-btn>
        </q-card-actions>
      </q-card>
    </q-dialog>
  `
})
