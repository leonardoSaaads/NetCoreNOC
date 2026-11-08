-- 0043: a situation's NOC case is opened on request, not by opening the situation (ADR #480).
-- Forward-only.
--
-- Until 0043 the trigger `situation_opens_a_case` (0034) gave every situation that became `open`
-- its case in the same transaction: confirming a grouping, correcting it, merging, a hand clear —
-- any gesture on a new situation put a case in the NOC queue. The owner's reading of a storm: ten
-- situations arrive, one of them is the root cause the others propagate from, and the NOC should
-- work that one. So the two decisions are separate now:
--
-- 1. **Is the grouping right?** An operator confirms (or corrects) it, and the situation is `open`
--    — the Broker's business, as before.
-- 2. **Does the NOC work it?** An operator presses *Start working this* on an `open` situation,
--    and only then is its case created (`POST /api/situations/{sid}/case`, the store's
--    `noc_case_open`, with the same priority mapping the trigger used).
--
-- Cases that already exist are untouched; an `open` situation without one simply waits for the
-- button.

DROP TRIGGER situation_opens_a_case ON situation;
DROP FUNCTION noc_case_open();
