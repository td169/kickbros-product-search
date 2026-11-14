# PRICING.md

How this app works out what to charge for an item — from the FR (French) retail price to your
final sale price and profit. This mirrors `recalc()` in `index.html` exactly; if the two ever
disagree, the code is right and this file needs updating.

## The three adjustable settings

Set in the Settings tab, saved to this browser's local storage. Defaults shown are what ships
with the app:

| Setting | Default | What it means |
|---|---|---|
| Détaxe refund | 12% | The % of the FR price you get back via the French tax refund |
| Target margin | 8.81% | The markup applied on top of cost to get a suggested sale price |
| Reseller discount | 3.04% | The % knocked off the sale price for the reseller/B2B price |

## Step by step

**1. Cost after détaxe refund (in EUR)**
```
costEUR = frPrice × (1 − détaxeRefund%)
```

**2. Cost in GBP** — converted at the live EUR→GBP rate (fetched from the Frankfurter API each
time you open or refresh a check):
```
costGBP = costEUR × exchangeRate
```

**3. Suggested sale price** — cost plus your target margin:
```
suggestedSalePrice = costGBP × (1 + targetMargin%)
```

**4. The savings cap** — the suggested price above is then checked against the table below. If
it would let the customer save more off UK RRP than their price band allows, the suggested price
gets pulled *up* to the cap floor instead:
```
maxSave = capFor(ukPrice)                        // from the table below
minSalePrice = ukPrice − maxSave
if suggestedSalePrice < minSalePrice:
    suggestedSalePrice = minSalePrice             // capped
```

**5. Your sale price field** — pre-filled with `suggestedSalePrice`, but it's directly editable.
Once you type into it, it stops auto-updating and everything downstream (profit, discount shown)
follows whatever's actually in that field — this is deliberate, since it's also where the *actual*
final price a customer paid gets recorded for a trip, not just the calculator's suggestion.
```
salePrice = user-typed value, if any, else suggestedSalePrice
```

**6. Reseller / B2B price** — always based on the suggested/capped price, never the manually
typed sale price (it's a separate business rule, not tied to what one customer happened to pay):
```
resellerPrice = suggestedSalePrice × (1 − resellerDiscount%)
```

**7. Profit**
```
profitAtSale     = salePrice − costGBP
profitAtReseller = resellerPrice − costGBP
totalProfit       = profitAtSale × quantity
totalSpend        = costGBP × quantity
```

**8. What's shown as "vs UK RRP"**
```
discountPct = 1 − (salePrice / ukPrice)
amountSaved = ukPrice − salePrice
```

## The savings cap table

The maximum a customer is ever allowed to save off UK RRP, by UK RRP band. This is a real
business rule, not a smoothed curve — note the £2,001–£3,000 band's cap (£230) is intentionally
*lower* than the £1,501–£2,000 band's (£270). That's not a bug.

| UK RRP band | Max saving allowed |
|---|---|
| up to £300 | £20 |
| £301 – £500 | £65 |
| £501 – £1,000 | £85 |
| £1,001 – £1,500 | £140 |
| £1,501 – £2,000 | £270 |
| £2,001 – £3,000 | £230 |
| £3,001 – £4,000 | £360 |
| £4,001 – £5,000 | £450 |
| £5,001 – £6,000 | £550 |
| above £6,000 | no cap applied |

## Worked example

FR price €1,090, UK RRP £950, exchange rate 1€ = £0.854:

```
costEUR   = 1090 × (1 − 0.12)        = 959.20
costGBP   = 959.20 × 0.854           = 819.16
suggested = 819.16 × (1 + 0.0881)    = 891.32

cap check: UK RRP £950 → band £501–£1,000 → max save £85
minSalePrice = 950 − 85 = 865
891.32 > 865, so no cap needed — suggested price stands.

salePrice      = £891.32
resellerPrice  = 891.32 × (1 − 0.0304) = £864.22
profitAtSale   = 891.32 − 819.16       = £72.16
discount       = 1 − (891.32 / 950)    = 6.2% below RRP (£58.68 saved)
```

If the UK RRP had instead been £700 (band up to £1,000, still £85 cap): `minSalePrice = 700 − 85
= £615`. Since £891.32 is well above that, the cap still wouldn't bite — it only kicks in when
the *margin-based* suggestion would have undercut the RRP by more than the band allows.
