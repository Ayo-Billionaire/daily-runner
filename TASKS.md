# Approval automation (22 Nov 2026) — tick in Airtable, it is scheduled

Finish line: Ayo ticks `Approve` on a lead row. Within a few hours the email is in Lumina (email 1 plus the 3 follow-ups, same thread) and the DM is on the sender's approved list. The row shows when it was scheduled. Nothing goes out unticked.

## Done
- [x] finder fix pushed 5059b18 (no-contact leads dropped) and d716907 (writes only columns that exist; "Found on", "Last posted" and the No Email table were deleted in Airtable on 22 Nov)
- [x] Lumina code read (working copy 552b4c5): PATCH /api/campaigns/:id with leadListIds on a live campaign runs reconcileAudience and enrols new list members (sequences.routes.ts:353-360, 413-418). Reconcile on its own only runs while a campaign has sent nothing (engine.ts:285).

## To do
- [ ] confirm finder test run 44998283645 green and rows landed (proves d716907)
- [ ] Ayo picks the Lumina target campaign (recommended: eae37ccd, live, 7 steps)
- [ ] Airtable: add `Approve` (checkbox) and `Scheduled on` (text) to the 3 lead tables
- [ ] script `approve/tick.py`: read ticked + unscheduled rows; email -> Lumina (POST lead to list, subject-line, first-line approve, PATCH leadListIds, read back); DM -> state/approved.json; write `Scheduled on`; dry-run flag
- [ ] workflow `approvals.yml` + clock worker slots 2 to 3 a day (GitHub cron has never fired on these repos, use the CF clock)
- [ ] dry run on a test row, read it back, then Ayo's go for one real lead
- [ ] memory + commitments update
