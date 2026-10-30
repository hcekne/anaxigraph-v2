# Design charter

This codebase will keep growing for years through requests nobody can predict. Optimise for
the hundredth change, not this one.

**Rooms and interfaces.** Think of the system as a large house. Each room (component) owns its
behavior and data behind a small, stable interface. Work inside one room should only require
testing that room plus the contract tests of its interface. If the interfaces stay the same,
the rest of the house keeps working. Plumbing that genuinely runs through several rooms
(shared infrastructure) is allowed, but keep it small, explicit and covered by its own tests.

**Uniform plug-ins for open-ended families.** When something comes in kinds that will keep
multiplying (integrations, providers, channels, report types), give the family one contract,
like hiring people: every contractor answers "what can you do for me?" with a list of
capabilities, takes requests in the same shape and is paid the same way, whether it is a
plumber or an accountant. Adding the next one should mean adding one self-contained unit plus
registering it, with no edits scattered through the rest of the code. Cross-cutting rules
(audit, quotas, caching, security) belong to the platform around the plug-ins, applied once,
not re-implemented inside each one.

**Ask before each change:**

- Which room owns this? Does it belong in an existing room, a new one, or the shared plumbing?
- If a thousand more requests like this one arrived, slightly different each time, would this
  still be the right shape? What would the next one cost?
- Which interfaces does this change? Who depends on them? What is the smallest blast radius?
- Which tests prove this room works on its own, without the rest of the house?

**CI follows the architecture.** Tests are organised by room. A change to one room selects that
room's tests plus the contract tests of anything depending on its interface; shared plumbing,
build files and unknown inputs select everything. Selection must be safe first and cheap
second. When coupling makes selection expensive, fix the coupling rather than the selector.

**Avoid ceremony.** No speculative layers, frameworks or services. Functions and a modular
monolith are fine. Extract a component when a second real use appears or a change keeps
touching several places at once.
