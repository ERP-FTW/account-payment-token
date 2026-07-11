# POS CardPointe POC Tipping

This module splits tip-at-sale support into a separate add-on that depends on `pos_cardpointe_poc`.

## Adds
- terminal tip configuration on `pos.cardpointe.terminal.config`
- `/v3/tip` terminal prompt before `authCard`
- tip/base amounts stored on `pos.payment`
- POS frontend support for terminal tip flow

## Install
1. Keep `pos_cardpointe_poc` as the base module.
2. Add this module to your addons path.
3. Update app list.
4. Install **POS CardPointe POC Tipping**.
