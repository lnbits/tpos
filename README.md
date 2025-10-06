# TPoS — _[LNbits](https://lnbits.com) extension_

[![OpenSats Supported](https://img.shields.io/badge/OpenSats-Supported-orange?logo=bitcoin&logoColor=white)](https://opensats.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-success?logo=open-source-initiative&logoColor=white)](./LICENSE)
[![Built for LNbits](https://img.shields.io/badge/Built%20for-LNbits-4D4DFF?logo=lightning&logoColor=white)](https://github.com/lnbits/lnbits)
[![Explore LNbits TPoS](https://img.shields.io/badge/Explore-LNbits%20TPoS-10B981?logo=puzzle-piece&logoColor=white&labelColor=065F46)](https://extensions.lnbits.com/tpos/)
[![Stripe Tap-to-Pay Wrapper](https://img.shields.io/badge/Stripe%20Tap--to--Pay-Wrapper-635BFF?logo=stripe&logoColor=white&labelColor=312E81)](https://github.com/lnbits/TPoS-Stripe-Tap-to-Pay-Wrapper)

**A shareable Bitcoin Lightning Point of Sale that runs directly in your browser.**  
No installation required — simple, fast, and safe for any employee to use.

_Optional:_ enable **One Checkout · Two Payment Rails** with the
[Stripe Tap-to-Pay Wrapper](https://github.com/lnbits/TPoS-Stripe-Tap-to-Pay-Wrapper) for Android.
Take card (fiat) via Stripe and Lightning payments side by side from a single TPoS flow.

_For video content about the TPoS extension, watch the [official demo](https://www.youtube.com/watch?v=8w4-VQ3WFrk)._

---

## Features

- **Create invoices** — instant Lightning QR invoices
- **Tipping** — percentages or rounding, split to a tip wallet
- **Item management** — products, cart, JSON import/export
- **OTC ATM** — LNURL Withdraw with PIN, limits, cooldown
- **Stripe fiat payment integration** — accept tap-to-pay via Stripe
- **Tax settings** — global/per-item, inclusive or exclusive

---

## Overview

TPoS lets you accept Bitcoin over the Lightning Network with ease. Each PoS instance is isolated from your main wallet — safe for staff use and multi-branch setups.  
Create as many TPoS instances as you need.

![Create TPoS](https://github.com/user-attachments/assets/68b875c8-95fd-45eb-acf4-3ad5a7af3cd7)

---

## Usage

1. **Enable** the extension.
2. **Create** a TPoS.  
   ![Create a TPoS](https://github.com/user-attachments/assets/68b875c8-95fd-45eb-acf4-3ad5a7af3cd7)
3. **Open** TPoS in the browser.  
   ![Open TPoS](https://github.com/user-attachments/assets/cc0a1362-c4ac-467e-9e7b-7e0206464882)
4. **Present** the invoice QR to the customer.  
   ![Invoice QR](https://github.com/user-attachments/assets/1d5341e2-cfba-45d5-b2c5-99f61a3d07a4)

---

## Receiving Tips

1. Create or edit a TPoS and activate _Enable tips_.  
   ![Enable tips](https://github.com/user-attachments/assets/02d4f3d7-ddfb-46a7-a33d-cbc3f3768278)
2. Fill in:
   - Wallet to receive tips
   - Tip percentages (press Enter after each)
   - If no values are set, a default _Rounding_ option is available
3. In TPoS, set an amount and click **OK**.  
   ![Enter amount](https://github.com/user-attachments/assets/563bc869-2d82-4e0f-9227-ec040bcf8f5e)
4. A tip dialog appears.  
   ![Tip selection dialog](https://github.com/user-attachments/assets/ae45b268-efd6-4d30-8840-efaa52430bcf)
5. Select a percentage or _Round to_.  
   ![Select tip or round](https://github.com/user-attachments/assets/707a0576-cc80-44db-9f0b-b496707ab3bc)
6. Present the updated invoice to the customer.  
   ![Invoice with tip](https://github.com/user-attachments/assets/c35a0a42-a620-49ca-b248-907f7923c5ce)
7. After payment the tip is sent to the defined wallet (e.g., employee wallet) and the rest to the main wallet.  
   ![Tip distribution](https://github.com/user-attachments/assets/b8fa8344-f164-4bd8-869d-6ca8d342ef9a)

---

## Adding Items to PoS

You can add items to a TPoS and use an item list for sales.

1. After creating or opening a TPoS, click the **expand** button.  
   ![Expand items](https://i.imgur.com/V468a7q.png)

   - Add items
   - Delete all items
   - Import / export items via JSON

2. Click _Add Item_ and fill in details (title and price are mandatory).  
   ![Add item dialog](https://i.imgur.com/dNQGFXa.png)

3. Or import a JSON with your products using this format:

```json
[
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
````

After adding products, the TPoS defaults to the **Items View** (PoS view):

![Items view](https://i.imgur.com/Adh0fdO.png)

Click **Add** to add to a cart / total:

![Add to cart](https://i.imgur.com/uZCQpZD.png)

Click **Pay** to show the invoice for the customer. To use the regular keypad TPoS, switch via the bottom button.

**Regular TPoS also supports adding to total:** enter a value and click `+`, repeat as needed, then click **OK**.
![Add custom value](https://i.imgur.com/DSOusVA.png)

---

## OTC ATM Functionality

1. Create or edit a TPoS and activate _Enable selling bitcoin_.  
   ![Enable selling bitcoin](https://github.com/user-attachments/assets/7f518c5b-ac4e-4562-a83b-ef5e4bc1f6e0)

2. Configure:
   - Maximum withdrawable per day
   - PIN to access ATM mode
   - Cooldown between withdrawals (min. 1 minute)

3. On TPoS, tap the **ATM** button and enter the PIN.  
   ![ATM PIN](https://github.com/user-attachments/assets/f410706f-aaca-488c-84b3-a7763eaa671c)  
   ![ATM screen](https://github.com/user-attachments/assets/ec21b9f9-95b9-4870-890c-2cec6cd50e93)

4. Set the amount to sell and present the **LNURLw** QR to the buyer.  
   ![Withdraw QR](https://github.com/user-attachments/assets/806fc6e6-9a75-4462-a3cc-382ce88ff1a6)

5. After successful withdrawal, a confirmation appears and TPoS exits ATM mode.  
   ![Withdrawal success](https://github.com/user-attachments/assets/8eee3198-061a-419f-99a2-f954a88ff845)


## Tax Settings

By default, tax is included in price. Set a default tax rate (%) (e.g., 13). Items can override this with their own tax value.

* **Tax Exclusive** — tax is applied on top of the unit price.
* **Tax Inclusive** — unit price already includes tax.

In the keypad PoS, the default tax is used and is always included in the value.

![Tax settings](https://github.com/user-attachments/assets/819c22b9-ab62-46bd-92f4-6339d13478d2)

---

## Powered by LNbits

LNbits empowers developers and merchants with modular, open-source tools for building Bitcoin-based systems — fast, free, and extendable.

[![Visit LNbits Shop](https://img.shields.io/badge/Visit-LNbits%20Shop-7C3AED?logo=shopping-cart\&logoColor=white\&labelColor=5B21B6)](https://shop.lnbits.com/)
[![Try myLNbits SaaS](https://img.shields.io/badge/Try-myLNbits%20SaaS-2563EB?logo=lightning\&logoColor=white\&labelColor=1E40AF)](https://my.lnbits.com/login)
[![Read LNbits News](https://img.shields.io/badge/Read-LNbits%20News-F97316?logo=rss\&logoColor=white\&labelColor=C2410C)](https://news.lnbits.com/)
[![Explore LNbits Extensions](https://img.shields.io/badge/Explore-LNbits%20Extensions-10B981?logo=puzzle-piece\&logoColor=white\&labelColor=065F46)](https://extensions.lnbits.com/)



