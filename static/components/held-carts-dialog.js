window.app.component('tpos-held-carts-dialog', {
  name: 'tpos-held-carts-dialog',
  props: ['show', 'heldCarts', 'formatDate'],
  emits: ['update:show', 'restore', 'delete'],
  template: `
    <q-dialog
      :model-value="show"
      position="bottom"
      @update:model-value="$emit('update:show', $event)"
    >
      <q-card class="lnbits__dialog-card">
        <q-card-section class="row items-center q-pb-none">
          <q-space />
          <q-btn icon="close" flat round dense v-close-popup />
        </q-card-section>
        <q-list separator>
          <q-item
            v-for="[key, value] in Object.entries(heldCarts || {})"
            :key="key"
            clickable
          >
            <q-item-section @click="$emit('restore', key)">
              <q-item-label class="text-bold" v-text="formatDate(key)">
              </q-item-label>
              <q-item-label caption lines="2" v-text="value.note"></q-item-label>
            </q-item-section>
            <q-item-section side top>
              <q-btn
                icon="delete"
                color="red"
                @click.stop="$emit('delete', key)"
                class="q-ml-sm"
                size="sm"
                round
              ></q-btn>
            </q-item-section>
          </q-item>
        </q-list>
      </q-card>
    </q-dialog>
  `
})
