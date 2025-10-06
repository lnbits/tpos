<div align="center">

  <h1>TPoS &mdash; <small><a href="https://lnbits.com">LNbits</a> extension</small></h1>

  <!-- Badges -->
  <p>
    <a href="https://opensats.org" target="_blank">
      <img alt="OpenSats Supported" src="https://img.shields.io/badge/OpenSats-Supported-orange?logo=bitcoin&logoColor=white">
    </a>
    <a href="./LICENSE">
      <img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-success?logo=open-source-initiative&logoColor=white">
    </a>
    <a href="https://github.com/lnbits/lnbits" target="_blank">
      <img alt="Built for LNbits" src="https://img.shields.io/badge/Built%20for-LNbits-4D4DFF?logo=lightning&logoColor=white">
    </a>
    <a href="https://github.com/lnbits/TPoS-Stripe-Tap-to-Pay-Wrapper" target="_blank">
      <img alt="Stripe Tap-to-Pay Wrapper" src="https://img.shields.io/badge/Stripe%20Tap--to--Pay-Wrapper-635BFF?logo=stripe&logoColor=white&labelColor=312E81">
    </a>
  </p>

  <p>
    <strong>A shareable Bitcoin Lightning Point of Sale that runs directly in your browser.</strong><br>
    No installation required &mdash; simple, fast, and safe for any employee to use.
  </p>

  <p style="max-width: 920px;">
    <small>
      Optional: enable <strong>One Checkout &middot; Two Payment Rails</strong> with our
      <a href="https://github.com/lnbits/TPoS-Stripe-Tap-to-Pay-Wrapper">Stripe Tap-to-Pay Wrapper</a> for Android.
      Take card (fiat) via Stripe and Lightning payments side by side from a single TPoS flow.
    </small>
  </p>

  <p>
    <small>
      For video content about the TPoS extension, watch the
      <a href="https://www.youtube.com/watch?v=8w4-VQ3WFrk">official demo</a>.
    </small>
  </p>

</div>



<h2>Features</h2>
<ul>
  <li><strong>Create invoices</strong> &mdash; instant Lightning QR invoices</li>
  <li><strong>Tipping</strong> &mdash; percentages or rounding, split to a tip wallet</li>
  <li><strong>Item management</strong> &mdash; products, cart, JSON import/export</li>
  <li><strong>OTC ATM</strong> &mdash; LNURL Withdraw with PIN, limits, cooldown</li>
  <li><strong>Stripe Fiat payment integration</strong> &mdash; accept tap-to-pay via Stripe</li>
  <li><strong>Tax settings</strong> &mdash; global/per-item, inclusive or exclusive</li>
</ul>

<h2>Overview</h2>

<p>
  TPoS lets you accept Bitcoin over the Lightning Network with ease. Each PoS instance is isolated from your main wallet &mdash; safe for staff use and multi-branch setups.
  Create as many TPoS instances as you need.
</p>

<div align="left">
  <img src="https://github.com/user-attachments/assets/68b875c8-95fd-45eb-acf4-3ad5a7af3cd7" alt="Create TPoS" width="520">
</div>



<h2>Usage</h2>

<ol>
  <li>Enable the extension.</li>
  <li>Create a TPoS.<br>
    <div align="left">
      <img src="https://github.com/user-attachments/assets/68b875c8-95fd-45eb-acf4-3ad5a7af3cd7" alt="Create a TPoS" width="460">
    </div>
  </li>
  <li>Open TPoS in the browser.<br>
    <div align="left">
      <img src="https://github.com/user-attachments/assets/cc0a1362-c4ac-467e-9e7b-7e0206464882" alt="Open TPoS" width="380">
    </div>
  </li>
  <li>Present the invoice QR to the customer.<br>
    <div align="left">
      <img src="https://github.com/user-attachments/assets/1d5341e2-cfba-45d5-b2c5-99f61a3d07a4" alt="Invoice QR" width="380">
    </div>
  </li>
</ol>



<h2>Receiving Tips</h2>

<ol>
  <li>Create or edit a TPoS and activate <em>Enable tips</em>.<br>
    <div align="left">
      <img src="https://github.com/user-attachments/assets/02d4f3d7-ddfb-46a7-a33d-cbc3f3768278" alt="Enable tips" width="440">
    </div>
  </li>
  <li>Fill in:
    <ul>
      <li>Wallet to receive tips</li>
      <li>Tip percentages (press Enter after each)</li>
      <li>If no values are set, a default <em>Rounding</em> option is available</li>
    </ul>
  </li>
  <li>In TPoS, set an amount and click <strong>OK</strong>.<br>
    <div align="left">
      <img src="https://github.com/user-attachments/assets/563bc869-2d82-4e0f-9227-ec040bcf8f5e" alt="Enter amount" width="440">
    </div>
  </li>
  <li>A tip dialog appears.<br>
    <div align="left">
      <img src="https://github.com/user-attachments/assets/ae45b268-efd6-4d30-8840-efaa52430bcf" alt="Tip selection dialog" width="440">
    </div>
  </li>
  <li>Select a percentage or <em>Round to</em>.<br>
    <div align="left">
      <img src="https://github.com/user-attachments/assets/707a0576-cc80-44db-9f0b-b496707ab3bc" alt="Select tip or round" width="440">
    </div>
  </li>
  <li>Present the updated invoice to the customer.<br>
    <div align="left">
      <img src="https://github.com/user-attachments/assets/c35a0a42-a620-49ca-b248-907f7923c5ce" alt="Invoice with tip" width="380">
    </div>
  </li>
  <li>After payment the tip is sent to the defined wallet (e.g., employee wallet) and the rest to the main wallet.<br>
    <div align="left">
      <img src="https://github.com/user-attachments/assets/b8fa8344-f164-4bd8-869d-6ca8d342ef9a" alt="Tip distribution" width="520">
    </div>
  </li>
</ol>



<h2>Adding Items to PoS</h2>

<p>You can add items to a TPoS and use an item list for sales.</p>

<ol>
  <li>After creating or opening a TPoS, click the expand button.<br>
    <div align="left">
      <img src="https://i.imgur.com/V468a7q.png" alt="Expand items" width="300">
    </div>
    <ul>
      <li>Add items</li>
      <li>Delete all items</li>
      <li>Import / export items via JSON</li>
    </ul>
  </li>
  <li>Click <em>Add Item</em> and fill in details (title and price are mandatory).<br>
    <div align="left">
      <img src="https://i.imgur.com/dNQGFXa.png" alt="Add item dialog" width="380">
    </div>
  </li>
  <li>Or import a JSON with your products using this format:</li>
</ol>

<pre>
<code>[
  {
    "image": "https://image.url",
    "price": 1.99,
    "title": "Item 1",
    "tax": 3.5,
    "disabled": false
  },
  {
    "price": 0.99,
    "title": "Item 2",
    "description": "My cool Item #2"
  }
]
</code>
</pre>

<p>After adding products, the TPoS defaults to the <strong>Items View</strong> (PoS view):</p>
<div align="left">
  <img src="https://i.imgur.com/Adh0fdO.png" alt="Items view" width="460">
</div>

<p>Click <strong>Add</strong> to add to a cart / total:</p>
<div align="left">
  <img src="https://i.imgur.com/uZCQpZD.png" alt="Add to cart" width="460">
</div>

<p>Click <strong>Pay</strong> to show the invoice for the customer. To use the regular keypad TPoS, switch via the bottom button.</p>

<p><strong>Regular TPoS also supports adding to total:</strong> enter a value and click <code>+</code>, repeat as needed, then click <strong>OK</strong>.<br>
<div align="left">
  <img src="https://i.imgur.com/DSOusVA.png" alt="Add custom value" width="300">
</div>
</p>



<h2>OTC ATM Functionality</h2>

<ol>
  <li>Create or edit a TPoS and activate <em>Enable selling bitcoin</em>.<br>
    <div align="left">
      <img src="https://github.com/user-attachments/assets/7f518c5b-ac4e-4562-a83b-ef5e4bc1f6e0" alt="Enable selling bitcoin" width="440">
    </div>
  </li>
  <li>Configure:
    <ul>
      <li>Maximum withdrawable per day</li>
      <li>PIN to access ATM mode</li>
      <li>Cooldown between withdrawals (min. 1 minute)</li>
    </ul>
  </li>
  <li>On TPoS, tap the <strong>ATM</strong> button and enter the PIN.<br>
    <div align="left">
      <img src="https://github.com/user-attachments/assets/f410706f-aaca-488c-84b3-a7763eaa671c" alt="ATM PIN" width="360">
      <img src="https://github.com/user-attachments/assets/ec21b9f9-95b9-4870-890c-2cec6cd50e93" alt="ATM screen" width="360">
    </div>
  </li>
  <li>Set the amount to sell and present the LNURLw QR to the buyer.<br>
    <div align="left">
      <img src="https://github.com/user-attachments/assets/806fc6e6-9a75-4462-a3cc-382ce88ff1a6" alt="Withdraw QR" width="360">
    </div>
  </li>
  <li>After successful withdrawal, a confirmation appears and TPoS exits ATM mode.<br>
    <div align="left">
      <img src="https://github.com/user-attachments/assets/8eee3198-061a-419f-99a2-f954a88ff845" alt="Withdrawal success" width="360">
    </div>
  </li>
</ol>



<h2>Tax Settings</h2>

<p>
  By default, tax is included in price. Set a default tax rate (%) (e.g., 13). Items can override this with their own tax value.
</p>

<ul>
  <li><strong>Tax Exclusive</strong> &mdash; tax is applied on top of the unit price.</li>
  <li><strong>Tax Inclusive</strong> &mdash; unit price already includes tax.</li>
</ul>

<p>
  In the keypad PoS, the default tax is used and is always included in the value.
</p>

<div align="left">
  <img src="https://github.com/user-attachments/assets/819c22b9-ab62-46bd-92f4-6339d13478d2" alt="Tax settings" width="380">
</div>

<div align="center" style="margin: 24px 0;">

  <h3 style="margin: 0 0 8px 0;">Powered by LNbits</h3>

  <p style="max-width: 820px; margin: 0 auto 14px auto; line-height: 1.55;">
    LNbits empowers developers and merchants with modular, open-source tools for building Bitcoin-based systems — fast, free, and extendable.
  </p>

  <p style="margin: 0;">
    <a href="https://shop.lnbits.com/" target="_blank" style="margin: 0 10px;">
      <img
        src="https://img.shields.io/badge/Visit-LNbits%20Shop-7C3AED?logo=shopping-cart&logoColor=white&labelColor=5B21B6"
        alt="LNbits Shop">
    </a>
    <a href="https://my.lnbits.com/login" target="_blank" style="margin: 0 10px;">
      <img
        src="https://img.shields.io/badge/Try-myLNbits%20SaaS-2563EB?logo=lightning&logoColor=white&labelColor=1E40AF"
        alt="myLNbits SaaS">
    </a>
    <a href="https://news.lnbits.com/" target="_blank" style="margin: 0 10px;">
      <img
        src="https://img.shields.io/badge/Read-LNbits%20News-F97316?logo=rss&logoColor=white&labelColor=C2410C"
        alt="LNbits News">
    </a>
<a href="https://extensions.lnbits.com/" target="_blank" style="margin: 0 10px;">
  <img
    src="https://img.shields.io/badge/Explore-LNbits%20Extensions-10B981?logo=puzzle-piece&logoColor=white&labelColor=065F46"
    alt="LNbits Extensions">
</a>

  </p>

</div>
