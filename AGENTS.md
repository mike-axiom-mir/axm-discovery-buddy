# AXM Agent Guidance

## Detail-density and composable capability principle

Quality is often the accumulated result of many small correct details, not one large generic upgrade.

- When improving a system, look for missing small, bounded capabilities, checks, parameters, passes, and repair operations that control specific details or failure modes.
- Prefer many reusable, inspectable, composable capabilities over one opaque "make it better" step when the smaller capabilities create real control or evidence.
- A machine should remain useful without AI: humans, explicit state, recipes, or deterministic logic can invoke the same capabilities directly.
- With AI, the model is primarily an interpretation and orchestration layer: it translates a higher-level goal into selections and combinations of the same underlying capabilities. The AI does not own those capabilities.
- A better reasoning model may improve goal interpretation and composition, while the underlying machine remains portable and usable without that model.
- Judge improvement by accumulated perceptual or functional detail, coherence, failure reduction, and fit to the goal—not by model size, resolution, benchmark score, or one broad upgrade alone.
- For visual, game, asset, animation, and video work, pay attention to small interacting details such as material variation, contact, timing, weight, secondary motion, lighting response, sound layering, asymmetry, wear, scale cues, camera behavior, and continuity.
- Do not fragment working systems merely for ideology. Add granularity where it creates useful control, reuse, diagnosis, repair, or quality.

**Working rule:** thousands of small good details and capabilities in the right places can improve a result more than one simple big upgrade.

## Canonical state and adaptive realization principle

When useful, separate **what exists** from **how it is expressed on a particular machine**.

- Canonical state/identity is authoritative. Rendering, UI, meshes, previews, audio paths, device skins, caches, and other realizations are replaceable expressions unless the repository explicitly defines otherwise.
- Preserve expression intent separately where needed: meaning, material character, motion weight, readability, atmosphere, hierarchy, sound intent, semantic detail, and other qualities that should survive changes in rendering cost.
- Prefer one truthful body with multiple realization contracts over manually divergent `mobile`, `lite`, `desktop`, `ultra`, or platform editions when the same canonical state can support them.
- Choose realization from canonical state + expression intent + measured machine capabilities + user policy. Adaptation may occur at launch or dynamically as GPU/CPU/RAM, battery, thermals, display, latency, storage, network, or user preference changes.
- A weak device should usually receive a cheaper expression, **not weaker truth**. Logical/world/design detail may remain rich even when the visible or audible realization is simplified.
- Identify non-degradable invariants explicitly. Examples include rules, fairness, hit detection, data integrity, core functionality, privacy promises, causal meaning, timing meaning, content identity, and authoritative state.
- Never let a lossy realization overwrite richer canonical state merely because that realization was rendered or edited on a weaker device. A projection/cache is not authority.
- Upgrading expression must not invent canonical facts. Downgrading expression must not erase canonical facts.
- Build bounded alternative realization paths where they add value: geometry detail, texture resolution, lighting, particles, simulation passes, post-processing, UI density, preview fidelity, audio richness, or analogous domain-specific expression layers.
- Do not force this split where representation itself is the canonical truth; apply it where truth and realization can honestly be separated.

**Working rule:** degrade expression, never truth; upgrade expression, never invent truth. One body may wake up differently on different machines while remaining the same thing.
