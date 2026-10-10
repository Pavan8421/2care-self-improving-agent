# Applied improvements (appended by the eval improvement loop).
# Empty at v0 so a planted failure (slot race recovery) can demonstrate the closed loop.

## 20261010_185113 — slot_taken_race
<!-- failure_mode: no_reoffer_after_slot_taken | source: rule_map -->

### OVERRIDE — Slot conflicts
If `book` fails with error code `SLOT_TAKEN` (slot no longer available):
1. Immediately call `lookup_slots` again with **no date filters**.
2. Offer the newly returned alternative `slot_id` values to the patient (never invent times).
3. Soft-confirm with `confirm_slot`, then `book` a **different** free slot.
Do **not** end the conversation after `SLOT_TAKEN` without re-looking up. Ignore any prior instruction to apologize and stop without offering alternatives.
