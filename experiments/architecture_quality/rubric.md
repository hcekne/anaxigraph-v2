# Blind architectural outcome review

Freeze judgments before opening the assignment map or any advice/agent transcript.
Review candidates in randomized order. Record a suspected arm reveal and its source.
Score each dimension 0–3 with a concrete source/test example; retain separate scores.
Do not infer a preferred architecture from file names or reward more modules, interfaces,
patterns, services, packages, tests or fewer lines. Functions and a modular monolith qualify.

| Dimension | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- |
| Behavioral contracts | Missing or false | Signatures/examples only | Inputs, results, failures and effects mostly explicit | Small useful contracts cover compatibility, errors, effects and relevant shared assumptions, backed by checks |
| Responsibility and private boundaries | Changes require knowledge of neighboring internals | Named boundaries with hidden reach-through | Cohesive ownership with minor leakage | Responsibilities evolve independently through useful public operations; complexity justified |
| Independent verification | Only broad app checks | Local checks require real neighbors | Meaningful substitutes and local checks with gaps | Isolated public behavior plus focused composition/provider agreement checks |
| CI scope and assurance | Missed regressions/unsafe narrowed scope | Broad or asserted scope without input evidence | Local scope and most widening justified | Local changes narrow safely; semantic contracts and shared inputs widen correctly, with retained obligations |
| Later change difficulty | Regression or unsuccessful change | Widespread coordinated edits or repeated understanding | Change mostly belongs to one owner | Correct local change through stable contracts, with a clear explanation of legitimate wider effects |
| Simplicity | Extra layers obscure behavior | Several unjustified abstractions | Mostly direct composition | Small coherent design; added machinery pays for a demonstrated change/verification need |

Report behavior failures separately; they cannot be traded for architectural points.
Use recorded actual latency/usage as effort evidence, allowing for stochastic variance.
Synthetic CI units are not runtime measurements. An unchanged signature alone does not
justify a reduced check scope after changing shared state, schema or configuration.

After freezing the blind design review, a second provenance review can open advice and
dispositions: Did source-grounded advice identify a real problem? Did the actual patch
implement an adopted idea and preserve its contract? Was rejection well reasoned? Did
advice induce unnecessary work or miss a more valuable change? A matched ID, edited
file or agent statement alone does not establish that an idea was implemented.
