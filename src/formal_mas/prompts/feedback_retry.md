<!-- Critic's deterministic feedback (Phase 6, agents/critic.py), inserted into the Generator's and the
     Autoformalizer's prompts on a retry through their feedback slot. No LLM writes it: Lean's
     message (which prints the goal state), the failed tactic block and the suggested real names. -->
USER:
The previous attempt at this statement failed (attempt {{attempt}}, error class: {{error_class}}).
{{hint}}

Failed tactic block:
```lean
{{tactics}}
```

Lean's report:
{{report}}
{{suggestions}}
Do not repeat the same mistake.

