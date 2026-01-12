window.app.component('order-receipt', {
  name: 'order-receipt',
  props: ['data', 'type'],
  data() {},
  template: `
    <div class="q-pa-md">
    <div class="text-center q-mb-xl">
    <p class='text-subtitle2 text-uppercase'>Order</p>
    </div>
    <q-table v-if="data.extra.details.items && data.extra.details.items.length > 0"
      dense
      class="q-ma-none"
      :hide-pagination="true"
      :rows-per-page-options="[0]"  
      :rows="data.extra.details.items"
      class="q-pa-none text-caption"
      :columns="[
          { name: 'title', label: 'Item', field: 'title' },
          { name: 'quantity', label: 'Qty', field: 'quantity' },
        ]"
      row-key="title"
        hide-bottom
    >
      <template v-slot:header="props">
        <q-tr :props="props">
          <q-th
            v-for="col in props.cols"
            :key="col.name"
            :props="props"
          >
            <span class="q-pa-none text-subtitle2 text-no-wrap" v-text="col.label"></span>
          </q-th>
        </q-tr>
      </template>
      <template v-slot:body="props">
        <q-tr :props="props">
          <q-td key="title" :props="props" class="q-pa-none">
            <div class="text-subtitle2" v-text="props.row.title"></div>
            <div
              v-if="props.row.note"
              class="text-subtitle2 text-italic"
              v-text="props.row.note"
            ></div>
          </q-td>
          <q-td key="quantity" :props="props" class="q-pa-none">
            <span class="text-subtitle2 text-no-wrap" v-text="props.row.quantity"></span>
          </q-td>
        </q-tr>
      </template>
    </q-table>
  </div>
    `
})
