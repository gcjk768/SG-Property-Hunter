#!/usr/bin/env bash
# Prints the four worked example cards with your profile from config.yaml.
# Your profile has no income, cash or CPF yet, so this script fills those three with
# placeholders through --set. Replace them with your own figures, or fill in config.yaml
# and delete the --set lines.
set -euo pipefail
P=(--set gross_monthly_income=10000 --set cash_available=300000 --set cpf_oa_balance=100000 --date 2026-10-02)
D=${DETAIL:-}

echo "=== 1. HDB resale, 4 room, S\$600,000 ==="
propbot analyse --category hdb_resale --name "Example 4 room resale flat" --area Tampines --address "Tampines Street 81" \
  --price 600000 --size-sqm 93 --tenure "99 year" --remaining-lease 68 --built 1995 --flat-type "4 ROOM" \
  --rent 3200 --cagr 3.0 "${P[@]}" $D

echo; echo "=== 2. Resale condo, OCR, S\$1,500,000 ==="
propbot analyse --category condo_resale --name "Example OCR condo, 3 bedroom" --area Punggol --address "Punggol Walk" \
  --segment OCR --price 1500000 --size 1076 --tenure "99 year" --remaining-lease 88 --built 2018 \
  --rent 4500 --cagr 2.5 "${P[@]}" $D

echo; echo "=== 3. Shophouse, commercial zoning, freehold, S\$4,000,000 ==="
propbot analyse --category shophouse --name "Example conservation shophouse" --area "Tanjong Pagar" --address "Duxton Road" \
  --price 4000000 --size 1800 --tenure freehold --zoning commercial --gst-seller no \
  --rent 12000 --cagr 3.0 "${P[@]}" $D

echo; echo "=== 4. BTO 4 room in a Plus project, S\$500,000 ==="
propbot analyse --category bto --name "Example BTO 4 room, Plus project" --area "Toa Payoh" \
  --price 500000 --size-sqm 93 --tenure "99 year" --remaining-lease 99 --completion 2030 --flat-type "4 ROOM" \
  --classification plus --rent 3600 --cagr 3.0 "${P[@]}" $D
