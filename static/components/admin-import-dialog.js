window.app.component('tpos-admin-import-dialog', {
  name: 'tpos-admin-import-dialog',
  props: ['dialog'],
  emits: ['import'],
  template: `
    <q-dialog v-model="dialog.show" position="top">
      <q-card class="q-pa-lg q-pt-xl lnbits__dialog-card">
        <q-card-section>
          <h6 class="text-subtitle1 q-my-none">
            <span v-text="'Importing ' + dialog.count + ' items'"></span>
          </h6>
        </q-card-section>
        <q-list bordered padding separator>
          <q-item v-for="item in dialog.data" :key="item.name">
            <q-item-section v-if="item.image" top avatar>
              <q-avatar>
                <img :src="item.image" style="object-fit: scale-down" />
              </q-avatar>
            </q-item-section>

            <q-item-section>
              <q-item-label v-text="item.title"></q-item-label>
              <q-item-label
                v-if="item.description"
                caption
                v-text="item.description"
              ></q-item-label>
            </q-item-section>

            <q-item-section side top>
              <q-badge :label="item.formattedPrice" />
            </q-item-section>
          </q-item>
        </q-list>
        <div class="row q-mt-lg">
          <q-btn outline color="primary" @click="$emit('import')"
            >Import</q-btn
          >
          <q-btn v-close-popup flat color="grey" class="q-ml-auto">Close</q-btn>
        </div>
      </q-card>
    </q-dialog>
  `
})
