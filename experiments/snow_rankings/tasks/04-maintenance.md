# Task 4 Subsequent change under preserved contracts

A traveler can ski five days but stay only three nights. Add independent `nights` (0..14, default
`days`) and `max_travel_hours` to the search API and frontend. Accommodation uses nights; lift-pass
costs continue to use ski days. Existing calls omitting both fields retain prior behavior. Apply
the travel-time filter consistently in every sort and in the UI. Add regression tests that verify
the changed components and unchanged transport/lift costs. Run all previous checks and commit.

Record how long locating the responsible code took, what contracts you preserved and whether
this change required edits across unrelated responsibilities. Do not redesign the whole app just
to implement two inputs. This is the chained-change measurement, not a new clean-slate build.
