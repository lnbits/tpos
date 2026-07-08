window.app.component('tpos-admin-item-dialog', {
  name: 'tpos-admin-item-dialog',
  props: ['dialog', 'categoryList'],
  emits: ['close', 'submit'],
  template: `
    <q-dialog v-model="dialog.show" position="top" @hide="$emit('close')">
      <q-card class="q-pa-lg q-pt-xl" style="width: 500px">
        <q-form @submit="$emit('submit')" class="q-gutter-md">
          <q-input
            filled
            dense
            v-model.trim="dialog.data.title"
            label="Title *"
          ></q-input>
          <q-input
            filled
            dense
            v-model.trim="dialog.data.description"
            label="Description"
          ></q-input>
          <q-input
            filled
            dense
            v-model.trim="dialog.data.image"
            label="Image URL"
          ></q-input>
          <q-input
            filled
            dense
            v-model.number="dialog.data.price"
            :label="\`Price (${dialog.data.currency})*\`"
          ></q-input>
          <q-select
            filled
            multiple
            dense
            emit-value
            v-model="dialog.data.categories"
            :options="categoryList"
            use-input
            use-chips
            hide-dropdown-icon
            input-debounce="0"
            new-value-mode="add-unique"
            label="Categories (hit enter to add values)"
          ></q-select>
          <q-input
            filled
            dense
            v-model.number="dialog.data.tax"
            label="Tax %"
            :hint="\`${dialog.taxInclusive ? 'Tax is included on unit price' : 'Tax is added on top of unit price'}. You can change behaviour on TPoS settings.\`"
          ></q-input>
          <q-checkbox
            v-model="dialog.data.disabled"
            label="Disable"
          ></q-checkbox>
          <div class="row q-mt-lg">
            <q-btn
              unelevated
              color="primary"
              :disable="!Boolean(dialog.data.title) || !Boolean(dialog.data.price)"
              type="submit"
              :label="dialog.data.id ? 'Update Item' : 'Create Item'"
            ></q-btn>
            <q-btn
              v-close-popup
              @hide="$emit('close')"
              flat
              color="grey"
              class="q-ml-auto"
              >Cancel</q-btn
            >
          </div>
        </q-form>
      </q-card>
    </q-dialog>
  `
})
