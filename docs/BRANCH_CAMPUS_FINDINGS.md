# Branch Campus Findings — parked for later

This app is currently scoped to **University Park only** (per explicit
instruction). Every major/minor build this session has been University
Park's own version. Along the way, research repeatedly surfaced real PSU
programs that only exist at branch ("Commonwealth") campuses or World
Campus, not at University Park. Those got skipped or substituted rather
than built — this file is where that research is kept, so that whenever
branch-campus support actually gets built, it's a lookup, not a re-scrape.

**Not exhaustive.** This only captures branch-campus findings that came up
incidentally while researching University Park programs. A real branch-campus
build-out needs its own systematic pass per campus, not just this list.

## How campus scoping shows up on bulletins.psu.edu

Every program page has a "Where can I complete this program?" section
listing its real campus(es). Many majors have multiple tiles on the
`/programs/` page — one per campus — with the *same* major name but a
different plan code and (often) a materially different curriculum per
campus. The pattern seen repeatedly:
- `<Major>, B.S. (Business)` or similar suffix → University Park
- `<Major>, B.S. (Behrend)` → Erie
- `<Major>, B.S. (Capital)` → Harrisburg
- `<Major>, B.S. (Berks)`, `(Altoona)`, `(Abington)` → those campuses

## Confirmed branch-campus-only or World-Campus-only programs

Found while researching University Park minors/majors this session —
each of these was checked directly against its own bulletin page's
"Where can I complete this program?" section, not assumed:

| Program | Real campus | Found while researching |
|---|---|---|
| Computer Science, Minor | Behrend (Erie) and Capital (Harrisburg) only — two separate pages, neither at UP | CPTSC minor (substituted with Computational Sciences) |
| Business Administration, Minor | Capital (Harrisburg), with an Abington option | Business & Management minor batch (substituted with Entrepreneurship and Innovation) |
| Management Information Systems, Minor | Behrend (Erie) only | Business & Management minor batch (substituted with Information Systems Management, a real Smeal/UP minor) |
| Human Resource Management, Minor | Capital (Harrisburg) | Business & Management minor batch (substituted with Labor and Human Resources, a real Liberal Arts/UP minor) |
| Business, Minor | "University College" — Commonwealth Campus administrative unit, not UP | Business & Management minor batch |
| Organizational Leadership, Minor | Explicitly designed for World Campus (online) students, not an in-person UP offering | Business & Management minor batch (skipped; not clearly UP) |
| Accounting, B.S. | Multiple campus-specific variants exist (Abington, Altoona, Behrend/Erie, Berks) alongside the real UP one (suffixed "(Business)") | General majors research — UP variant is what's built |

## What a real branch-campus build-out would need

1. **Per-campus program lists.** The `/programs/` page's tile hover text
   ("Campus: X") is the source of truth per program — it needs a systematic
   pass, not incidental discovery like this file.
2. **A campus dimension on `degree_plans`/`minors` file naming or metadata.**
   Right now every plan file assumes University Park; a real multi-campus
   model needs either a `campus` field in the JSON or a folder-per-campus
   layout, plus catalog data scoped per campus (some branch campuses don't
   offer every course a UP plan expects).
3. **Frontend campus selector wired to filtering, not just display.** The
   campus dropdown in the chat panel already exists and is sent to the
   backend, but `/api/degree-plans` and `/api/minor-plans` don't currently
   filter by it — every major/minor returned is the UP one regardless of
   the selected campus. That's the actual gap to close first.
4. **Re-verify prereq/catalog data per campus.** Course offerings and even
   course numbering can differ by campus (e.g., some campuses cap out
   before 400-level courses); a UP-verified prereq chain isn't guaranteed
   to hold at a branch campus.

## Next step, when it's time

Start with #3 above (wire the existing campus selector to actually filter),
since the UI plumbing already exists — that's the highest-leverage, lowest-effort
first slice, and would surface exactly how much of #1/#2/#4 is needed for
the specific campus picked first.
