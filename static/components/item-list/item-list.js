async function itemList(path) {
  const template = await loadTemplateAsync(path)

  Vue.component('item-list', {
    name: 'item-list',
    props: ['items', 'inclusive', 'format', 'currency', 'add-product'],
    template,

    data: function () {
      return {}
    },
    computed: {},
    methods: {
      addToCart(item) {
        this.$emit('add-product', item)
      }
    },
    created() {}
  })
}
