# TPoS - <small>[LNbits](https://github.com/lnbits/lnbits) extension</small>

<small>For more about LNBits extension check [this tutorial](https://github.com/lnbits/lnbits/wiki/LNbits-Extensions)</small>

## A Shareable PoS (Point of Sale) that doesn't need to be installed and can run in the browser!

An easy, fast and secure way to accept Bitcoin, over Lightning Network, at your business. The PoS is isolated from the wallet, so it's safe for any employee to use. You can create as many TPOS's as you need, for example one for each employee, or one for each branch of your business.

### Usage

1. Enable extension
2. Create a TPOS\
   ![create](https://imgur.com/8jNj8Zq.jpg)
3. Open TPOS on the browser\
   ![open](https://imgur.com/LZuoWzb.jpg)
4. Present invoice QR to customer\
   ![pay](https://imgur.com/tOwxn77.jpg)

## Receiving Tips

1. Create or edit an existing TPOS and activate _Enable tips_
   ![tips](https://i.imgur.com/VRKPNop.png)
2. Fill in the necessary fields:
   - select a wallet to receive tips in
   - define the tip (percentage)
   - hit _Enter_ after every option
   - if no values are defined, a default _Rounding_ option will be available. Round the check to a defined value.
3. When using the TPOS, set a value to receive as normal and hit **OK**
   ![tip amnt](https://i.imgur.com/Vyh0kqx.png)
4. A new dialog will show to define a tip
   ![set tip 1](https://i.imgur.com/1xxrAse.png)
   - select the % or choose _Round to_ to round the value
     ![set tip 2](https://i.imgur.com/gv48S8U.png)
5. Present the invoice to the customer, with the updated amount with the tip
   ![pay tip](https://i.imgur.com/WuaRzph.png)
   - after payment, the tip amount is sent to the defined wallet (for example an employees wallet) and the rest to the main wallet
     ![paym 1](https://i.imgur.com/zvDf1y5.png)

## OTC ATM functionality

1. Create or edit an existing TPOS and activate _Enable selling bitcoin_
   ![atm](https://i.imgur.com/WF3jiFb.png)
2. Fill in the necessary fields:

   - set a maximum withdrawable per day
   - define a Pin to access the functionality
   - define a _cool down_ period between withdraws, to avoid exploitation (min. 1 minute)

3. When using the TPOS, you'll see the **ATM** button, click and enter Pin
   ![atm pin](https://i.imgur.com/5QMYXX7.png)
4. Set amount to sell and click **OK**
   ![atm amount](https://i.imgur.com/V3jlfhV.png)
5. Show the LNURLw QR code to the buyer
   ![atm withdraw](https://i.imgur.com/rYXtn93.png)
6. After successful withdraw, the green check will show and TPOS exists ATM mode
   ![atm success](https://i.imgur.com/FaHltvW.png)
