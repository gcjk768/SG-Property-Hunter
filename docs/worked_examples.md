# Worked examples at the step 3 stop

These are the four `propbot analyse` cards the brief asked for, computed with no network. Run `bash docs/worked_examples.sh` to print them again, or `DETAIL=--detail bash docs/worked_examples.sh` for every working figure, year by year.

## What these cards assume

- **Profile.** Everything comes from `config.yaml`: Singapore citizen, 29, single, first timer, buying alone, ranked intents rent out, live then sell, flip, 10 year hold, 4% benchmark, 35% own limit.
- **Placeholders.** Three profile fields are 0 in your config, so the script fills them for this run only: income 10,000 a month, cash 300,000, CPF OA 100,000. The bot refuses to start with income 0, so put your real figures in before trusting any verdict.
- **Rates.** No SORA is stored yet (data arrives in step 4), so today's bank rate is assumed at your long run rate of 3.0%, and every card says so. The HDB loan rate is the CPF OA rate of 2.5% plus 0.1%, both from the rules file.
- **Inputs.** Prices, sizes, leases, rents and growth rates are typed in example inputs, not market data. No comparables are loaded, so every card shows Market unknown and loses half a point for thin evidence.

## 1. HDB resale, 4 room, S$600,000

Inputs: price 600,000; 93 sqm; 99 year lease, 68 years left; built 1995; market rent 3,200 a month; growth 3.0% a year (typed in).

```
#hdbresale HDB resale · 1 of 1 · Tampines
Example 4 room resale flat
Tampines Street 81 · 99 year, 68 years left · 1,001 sqft · built 1995
Price: S$600,000 (599 psf), typed in from your input on 2026-10-02
Market: unknown, no comparable sales loaded
Rent: about S$3,200/month (6.4% gross), typed in
Eligibility: Not eligible yet, singles must be 35 or older; you are 29, eligible from age 35, in about 6 years (2032), or now if you buy with a fiance, a spouse or your parents
Upfront: S$212,100
Downpayment S$150,000 (cash at least S$30,000, CPF up to S$100,000, extra cash S$20,000)
BSD S$12,600 · ABSD S$0 (0%) · Legal and valuation S$3,500 · Agent S$6,000 · Renovation S$40,000 budgeted · Furnishing S$10,000 in year 5, not upfront
Loan: S$450,000 (75.0% LTV, bank) over 25 years at 3.0%, assumed → S$2,134/month
Stress test at 4.0%: S$2,375/month, TDSR 23.8% (limit 55), MSR 23.8% (limit 30), your own limit 35%, this loan uses 21.3%
Monthly if rented out from year 6: rent S$3,447 minus instalment, maintenance, tax and vacancy = S$160

10 year outlook, sell in year 10:
Bear 0.0%/yr: net S$77,000, IRR 3.7%
Base 3.0%/yr: net S$263,000, IRR 9.4% (growth from typed in)
Bull 5.0%/yr: net S$418,000, IRR 12.7%
Break even year 5 · Fair price for your 4% target: above S$900,000
If rates are 1 point higher: base IRR 8.1% · If growth is 1 point lower: 7.7%
MOP: 5 years, earliest sale or whole unit rental 2031-10 (HDB resale) · SSD until 2030-10
Sell at MOP in year 5: base net S$104,000, IRR 9.1%
Year by year, base case (value starts at the price):
Yr    Value    Change  Equity    If sold
 1     614k     +2.3%    176k     locked
 2     628k     +2.3%    203k     locked
 3     642k     +2.3%    230k     locked
 4     657k     +2.3%    258k     locked
 5     672k     +2.3%    287k      +104k
 6     687k     +2.3%    316k      +136k
 7     703k     +2.3%    347k      +168k
 8     719k     +2.3%    378k      +201k
 9     731k     +1.8%    406k      +232k
10     744k     +1.8%    435k      +263k
Biggest drop: none in the base case · Best year: 1 (+2.3%) · Lease decay over 10 years: S$55,417, 0.7% a year then 1.2% a year
Signals: lease 58 years at exit
Flags: thin evidence
Verdict: Not eligible yet, eligible from age 35, in about 6 years (2032), or now if you buy with a fiance, a spouse or your parents
Model estimate, not financial advice. Rules as of 2026-10-02. Found 2026-10-02 14:09 SGT

[2429 characters; Telegram limit 4096]
```

**Check by hand**

- **Eligibility.** Singles must be 35 to buy an HDB flat, so the card says Not eligible yet and hides the verdict words. The numbers below are what it would cost once you qualify.
- **BSD.** 1% of 180,000 is 1,800. 2% of the next 180,000 is 3,600. 3% of the remaining 240,000 is 7,200. Total 12,600.
- **Loan.** The HDB loan needs income within the 7,000 singles ceiling, and the placeholder income is 10,000, so the bank loan is used. A bank loan on an HDB flat keeps the full 75% LTV only up to 25 years, so the loan is 450,000 over 25 years.
- **Instalment.** 450,000 x 0.0025 / (1 minus 1.0025 to the power of minus 300) = 2,133.95 a month at 3%. At the 4% stress floor it is 2,375.27, which is 23.8% of 10,000 for both TDSR and MSR.
- **Downpayment.** 150,000 = 30,000 minimum cash (5%) + 100,000 CPF + 20,000 extra cash.
- **Upfront.** 150,000 + 12,600 BSD + 3,000 legal + 500 valuation + 6,000 agent (1%) + 40,000 renovation = 212,100. Furnishing of 10,000 is paid in year 5, when renting becomes allowed.
- **Value.** 3% growth each year, less 0.7% lease decay while 61 to 68 years are left, then 1.2%. Year 10: 600,000 x 1.03^10 x 0.993^8 x 0.988^2 = 744,100.
- **Years 1 to 5 (MOP).** You live in the flat, so rent saved counts: 3,200 x 12, growing 1.5% a year. Owner occupied property tax on an annual value of 38,400 is 4% of 26,400 = 1,056.
- **Years 6 to 10.** Rented for 11 months a year. Non owner occupied property tax applies. Tax on rent is 15% x (85% of rent minus loan interest).
- **Exit in year 10.** 744,100 minus agent fee with GST of 16,221 (2% plus 9% GST), minus legal of 3,657 (3,000 grown 2% a year), minus loan of 309,008, gives 415,214. Of that, 128,008 goes back to your CPF (100,000 x 1.025^10, so 28,008 is interest).
- **Result.** The flows are minus 212,100 at the start, then the yearly flows, then the sale: net 263,237, IRR 9.4%.

## 2. Resale condo, OCR, S$1,500,000

Inputs: price 1,500,000; 1,076 sqft; 99 year lease, 88 years left; built 2018; market rent 4,500; growth 2.5% (typed in).

```
#condo Resale condo · 1 of 1 · Punggol
Example OCR condo, 3 bedroom
Punggol Walk · 99 year, 88 years left · 1,076 sqft · built 2018
Price: S$1,500,000 (1,394 psf), typed in from your input on 2026-10-02
Market: unknown, no comparable sales loaded
Rent: about S$4,500/month (3.6% gross), typed in
Eligibility: you can buy this, first property, ABSD 0%, bank loan only
Upfront: S$463,100
Downpayment S$375,000 (cash at least S$75,000, CPF up to S$100,000, extra cash S$200,000)
BSD S$44,600 · ABSD S$0 (0%) · Legal and valuation S$3,500 · Renovation S$30,000 budgeted · Furnishing S$10,000
Cash needed S$363,100 against your S$300,000, short by S$63,100
Loan: S$1,125,000 (75.0% LTV, bank) over 30 years at 3.0%, assumed → S$4,743/month
Stress test at 4.0%: S$5,371/month, TDSR 53.7% (limit 55), your own limit 35%, this loan uses 47.4%
Monthly if rented out: rent S$4,500 minus instalment, maintenance, tax and vacancy = loss of S$1,861

10 year outlook, sell in year 10:
Bear minus 0.5%/yr: net loss of S$148,000, IRR minus 3.0%
Base 2.5%/yr: net S$332,000, IRR 4.8% (growth from typed in)
Bull 4.5%/yr: net S$730,000, IRR 8.9%
Break even year 5 · Fair price for your 4% target: S$1,726,000
If rates are 1 point higher: base IRR 3.5% · If growth is 1 point lower: 2.5%
SSD until 2030-10 (no MOP for private)
Year by year, base case (value starts at the price):
Yr    Value    Change  Equity    If sold
 1   1,538k     +2.5%    436k  loss 332k
 2   1,576k     +2.5%    499k  loss 236k
 3   1,615k     +2.5%    563k  loss 134k
 4   1,656k     +2.5%    629k   loss 28k
 5   1,697k     +2.5%    697k       +84k
 6   1,740k     +2.5%    767k      +132k
 7   1,783k     +2.5%    838k      +181k
 8   1,828k     +2.5%    912k      +233k
 9   1,868k     +2.2%    982k      +281k
10   1,909k     +2.2%  1,053k      +332k
Biggest drop: none in the base case · Best year: 1 (+2.5%) · Lease decay over 10 years: S$11,363, 0.3% a year
Signals: lease 78 years at exit
Flags: thin evidence, out of reach
Verdict: Not affordable (1.5/5), best as live then sell
Needs S$63,100 more cash than you have.
Model estimate, not financial advice. Rules as of 2026-10-02. Found 2026-10-02 14:09 SGT

[2198 characters; Telegram limit 4096]
```

**Check by hand**

- **BSD.** 1,800 + 3,600 + 19,200 (3% of 640,000) + 20,000 (4% of 500,000) = 44,600. ABSD is 0% for a citizen's first property.
- **Loan.** 75% of 1,500,000 = 1,125,000 over 30 years, since 65 minus 29 is more than 30. At 3%: 4,743.05 a month. At the 4% stress floor: 5,370.92, which is TDSR 53.7%, just under 55%.
- **Own limit.** The instalment is 47.4% of income, above your own 35% limit, so no affordability point.
- **Cash.** Upfront is 375,000 + 44,600 + 3,500 + 30,000 + 10,000 = 463,100. CPF covers 100,000, so cash needed is 363,100. Against the placeholder 300,000 cash that is 63,100 short, hence Not affordable.
- **Rent.** 4,500 x 11 months = 49,500 in year 1. Property tax on an annual value of 54,000 = 12% x 30,000 + 20% x 15,000 + 28% x 9,000 = 9,120. Tax on rent = 15% x (42,075 minus 33,429 interest) = 1,297.
- **Monthly.** (49,500 minus 56,917 instalments minus 4,200 maintenance minus 9,120 minus 300 minus 1,297) / 12 = loss of 1,861 a month.
- **Value.** No decay while more than 80 years are left, then 0.3%. Year 10: 1,500,000 x 1.025^10 x 0.997^2 = 1,908,623.
- **Exit in year 10.** 1,908,623 minus 41,608 agent with GST, minus 3,657 legal, minus 855,223 loan = 1,008,136. Net 331,711, IRR 4.8%.
- **Flip.** Selling in year 3 pays SSD of 8% of the value (129,227), because this purchase falls under the post July 2025 regime.

## 3. Shophouse, commercial zoning, freehold, S$4,000,000

Inputs: price 4,000,000; 1,800 sqft; freehold; commercial zoning; seller not GST registered; market rent 12,000; growth 3.0% (typed in).

```
#shophouse Shophouse · 1 of 1 · Tanjong Pagar
Example conservation shophouse
Duxton Road · freehold · 1,800 sqft
Price: S$4,000,000 (2,222 psf), typed in from your input on 2026-10-02
Market: unknown, no comparable sales loaded
Rent: about S$12,000/month (3.6% gross), typed in
Eligibility: you can buy this, conservation status may restrict works, no CPF for commercial property
Upfront: S$3,148,551
Downpayment S$2,975,451 (cash at least S$2,975,451, CPF up to S$0, extra cash S$0)
BSD S$169,600 · ABSD S$0 (0%) · Legal and valuation S$3,500
CPF: no CPF for this property type
Cash needed S$3,148,551 against your S$300,000, short by S$2,848,551
Loan: S$1,024,549 (25.6% LTV, bank, planning LTV, capped by TDSR) over 30 years at 3.0%, assumed → S$4,320/month
Stress test at 5.0%: S$5,500/month, TDSR 55.0% (limit 55), your own limit 35%, this loan uses 43.2%
Monthly if rented out: rent S$12,000 minus instalment, maintenance, tax and vacancy = S$3,180

10 year outlook, sell in year 10:
Bear 0.0%/yr: net S$417,000, IRR 1.3%
Base 3.0%/yr: net S$1,763,000, IRR 4.8% (growth from typed in)
Bull 5.0%/yr: net S$2,878,000, IRR 7.0%
Break even year 2 · Fair price for your 4% target: S$5,393,000
If rates are 1 point higher: base IRR 4.5% · If growth is 1 point lower: 3.6%
No MOP and no SSD for this property type
Year by year, base case (value starts at the price):
Yr    Value    Change  Equity    If sold
 1   4,120k     +3.0%  3,117k   loss 86k
 2   4,244k     +3.0%  3,262k       +96k
 3   4,371k     +3.0%  3,413k      +283k
 4   4,502k     +3.0%  3,567k      +477k
 5   4,637k     +3.0%  3,726k      +676k
 6   4,776k     +3.0%  3,890k      +881k
 7   4,919k     +3.0%  4,059k    +1,092k
 8   5,067k     +3.0%  4,233k    +1,309k
 9   5,219k     +3.0%  4,412k    +1,533k
10   5,376k     +3.0%  4,597k    +1,763k
Biggest drop: none in the base case · Best year: every year about +3.0% · Lease decay over 10 years: none, freehold
Signals: none loaded yet
Flags: thin evidence
Verdict: Not affordable (2.5/5), best as rent out
Model estimate, not financial advice. Rules as of 2026-10-02. Found 2026-10-02 14:09 SGT

[2142 characters; Telegram limit 4096]
```

**Check by hand**

- **Zoning.** Commercial zoning means no ABSD, no SSD, no CPF and no furnishing. GST is 0 because the example seller is not GST registered. With an unknown seller the card adds 9% GST "if seller is GST registered".
- **BSD.** The non residential table: 1,800 + 3,600 + 19,200 + 20,000 + 5% of 2,500,000 (125,000) = 169,600.
- **Loan.** The commercial LTV planning figure is 70% (2,800,000). TDSR is lower: 55% of 10,000 = 5,500 a month at the 5% non residential floor over 30 years, so the loan is 1,024,549 and TDSR binds.
- **Cash.** Downpayment is 2,975,451, all in cash. Upfront is 3,148,551, which is 2,848,551 more than the placeholder cash, hence Not affordable.
- **Rent.** 12,000 x 10 months (2 months commercial vacancy) = 120,000. Property tax is 10% of an annual value of 144,000 = 14,400. Tax on rent uses actual expenses: 15% x (120,000 minus 30,444 interest minus 4,800 maintenance minus 14,400 minus 300) = 10,508.
- **Exit in year 10.** Value 4,000,000 x 1.03^10 = 5,375,666. Net 1,763,171, IRR 4.8%.

## 4. BTO 4 room in a Plus project, S$500,000

Inputs: price 500,000; 93 sqm; Plus project; completion 2030; market rent 3,600 (used for rent saved only); growth 3.0% (typed in).

```
#bto BTO · 1 of 1 · Toa Payoh
Example BTO 4 room, Plus project
99 year, 99 years left · 1,001 sqft · completion 2030
Price: S$500,000 (499 psf), typed in from your input on 2026-10-02
Market: unknown, no comparable sales loaded
Rent: about S$3,600/month (8.6% gross), typed in
Eligibility: Not eligible yet, singles must be 35 or older; you are 29, household income S$10,000 is above the BTO singles ceiling of S$7,000, eligible from age 35, in about 6 years (2032), or now if you buy with a fiance, a spouse or your parents
Upfront: S$168,100
Downpayment S$125,000 (cash at least S$25,000, CPF up to S$100,000, extra cash S$0)
BSD S$9,600 · ABSD S$0 (0%) · Legal and valuation S$3,500 · Renovation S$30,000 budgeted
Option fee S$2,000, 20% at the Agreement for Lease, the rest of the downpayment and the loan at key collection
Loan: S$375,000 (75.0% LTV, bank) over 25 years at 3.0%, assumed → S$1,778/month
Stress test at 4.0%: S$1,979/month, TDSR 19.8% (limit 55), MSR 19.8% (limit 30), your own limit 35%, this loan uses 17.8%
Monthly if rented out: not allowed, the whole flat cannot be rented out

10 year outlook: a sale in year 10 is not allowed because the MOP ends in year 14, so these sell in year 14:
Bear 0.0%/yr: net S$275,000, IRR 10.2%
Base 3.0%/yr: net S$510,000, IRR 13.8% (growth from typed in)
Bull 5.0%/yr: net S$725,000, IRR 16.1%
Break even year 14 · Fair price for your 4% target: above S$750,000
If rates are 1 point higher: base IRR 13.1% · If growth is 1 point lower: 12.6%
MOP: 10 years from key collection, earliest sale, whole flat rental never allowed, 2040-10 (Plus flat) · SSD until 2030-10
Subsidy recovery on resale: 6% of the price, assumed from earlier PLH projects; check the project's figure
Year by year, base case (value starts at the price):
Yr    Value    Change  Equity    If sold
 1     515k     +3.0%    115k     locked
 2     530k     +3.0%    130k     locked
 3     546k     +3.0%    146k     locked
 4     563k     +3.0%    188k     locked
 5     580k     +3.0%    215k     locked
 6     597k     +3.0%    243k     locked
 7     615k     +3.0%    272k     locked
 8     633k     +3.0%    301k     locked
 9     652k     +3.0%    332k     locked
10     672k     +3.0%    363k     locked
11     692k     +3.0%    396k     locked
12     713k     +3.0%    429k     locked
13     734k     +3.0%    463k     locked
14     756k     +3.0%    499k      +510k
Biggest drop: none in the base case · Best year: every year about +3.0% · Lease decay over 14 years: none in these years
Signals: lease 89 years at exit
Flags: thin evidence
Verdict: Not eligible yet, eligible from age 35, in about 6 years (2032), or now if you buy with a fiance, a spouse or your parents
Model estimate, not financial advice. Rules as of 2026-10-02. Found 2026-10-02 14:09 SGT

[2818 characters; Telegram limit 4096]
```

**Check by hand**

- **Eligibility.** A single person under 35 cannot buy a BTO flat, and singles can buy only a 2 room Flexi BTO even at 35. The card is Not eligible yet.
- **Loan.** The HDB loan is not available at the placeholder income (above the 7,000 singles ceiling), so the bank loan applies: 75% = 375,000 over 25 years, 1,778.29 a month at 3%.
- **Staging.** Option fee 2,000, then 20% at the Agreement for Lease (the bank loan rate; it would be 10% with an HDB loan), then the rest of the downpayment (25,000) at key collection in 2030. CPF covers 75,000 at the Agreement for Lease and 25,000 at keys. The minimum cash of 25,000 is the option fee plus 23,000.
- **MOP.** It is 10 years from key collection, so the first allowed sale is year 14 (2040). The card therefore sells in year 14, not year 10, and says so.
- **Rent.** A Plus flat can never be rented out whole, so it earns no rent. Rent saved counts once you live in it (from year 5). Nothing is earned during the 4 building years.
- **Subsidy recovery.** No project figure was typed in, so it uses the earlier PLH figure of 6%, labelled as an assumption: 6% x 756,295 = 45,378 at year 14.
- **Exit in year 14.** Value 500,000 x 1.03^14 = 756,295. Proceeds 432,965, of which 137,975 goes back to CPF. Net 510,418, IRR 13.8%. Most of the gain is rent saved and the low entry price.
