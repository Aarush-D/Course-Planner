-- One-off cleanup: removes disposable test data left behind by a live QA
-- pass against the advisor-workspace schema (0001/0003). Not a schema
-- migration -- doesn't belong in supabase/migrations/, since that
-- directory is meant to be replayable schema history, and a DELETE tied to
-- one test run's specific rows isn't that. Kept here, as a real file
-- (same "reproducible, not just the thing in the dashboard" reasoning as
-- the migrations), so future QA passes have a template to copy instead of
-- hand-writing throwaway DELETEs each time.
--
-- Safe to run any time after 0003 (or before -- these tables/policies
-- already exist as of 0001). None of the app's own anon/authenticated
-- Postgres roles are ever granted DELETE (by design -- nothing in the
-- product itself deletes rows), so this can only be run by a human, via
-- the Supabase SQL Editor, which uses the project owner's elevated
-- privileges rather than the app's own keys.
--
-- To reuse for a future QA pass: swap the match values below for whatever
-- marker the new disposable test data used (a distinctive student_label,
-- display_name, or note), then run in the SQL Editor.

delete from meeting_proposals where note = 'QA test meeting';
delete from review_requests where student_label = 'QA Test Student';
delete from advisor_profiles where display_name = 'QA Test Advisor';

-- Live-verified the new "See reviews" modal (2026-09-01) with a real
-- inserted rating -- course_ratings grants anon/authenticated INSERT and
-- SELECT only, never DELETE (see migration 0004), so this can't be
-- cleaned up by the app itself either.
delete from course_ratings where course_code = 'STAT 318' and review_body like 'Solid intro to probability%';

-- Confirming migration 0006 was still unapplied (2026-09-01) meant proving
-- the advisor self-promotion hole was ACTUALLY still open, live -- a
-- throwaway account really did insert itself into advisor_profiles with
-- zero vetting. The row this created is already covered by the
-- 'QA Test Advisor' delete above; this just also removes the throwaway
-- auth.users row itself (SQL Editor only -- auth.users isn't reachable
-- via the app's own keys either).
delete from auth.users where email in ('qa-advisor-gate-probe@example.com', 'qa-multiplan-test@example.com');

-- Re-verified 0006/0007/0008 immediately after you ran them (2026-09-01)
-- -- confirmed the direct-insert path is correctly rejected now and the
-- RPC/schema changes are live. One more throwaway account from that check.
delete from auth.users where email = 'qa-verify-0006@example.com';
