<!-- Short prompt texts assembled by the code, one per line as `key: text` (prompting.fragment). `\n` is a
     newline and {name} a value. hint_<class>: the Critic's advice per error class of Eq. 11. -->
hint_syntax: The tactic block was rejected before or while parsing; keep the proof idea and fix the Lean syntax or the forbidden construct.
hint_missing_lemma: A name does not exist in this Mathlib; use a real name or prove the fact with tactics.
hint_tactic_failure: The tactics did not close the goal; change the approach using the goal state below.
hint_misformalization: The error is in the statement, not in the proof; a proof attempt must work with the statement as written.
hint_timeout: The proof was too slow; use simpler tactics and smaller steps.
hint_planning: The subgoal may not follow from its hypotheses; try another way to prove it.
real_names: \nReal names close to the missing one: {names}
advice: \nAdvice: {advice}
no_tactics: (no tactic block)
no_subgoals: (none)
replan_subgoal: {stage} failed after {attempts} attempts ({error_class}). {report}
replan_plan: {reason}. {report}
no_lemmas: (none retrieved)
no_sketch: (no sketch)
no_lemma_names: (none)
no_informal: (none)
no_strategy: none
check_strategy: the claimed natural-language proof
final_answer: \n\nFinal answer: {answer}
closing_step: Closing step (Lean, already checked):\n```lean\n{closing}\n```
