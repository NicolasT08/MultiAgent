import Mathlib

/-!
Dump the names of the public theorems of `Init`, `Std` and `Batteries` visible after
`import Mathlib`, as JSON Lines (`{"name", "module"}`).

Run from `lean_project/` (done by `scripts/build_index.py --extra-names`):

    FORMAL_MAS_NAMES_OUT=<file> lake env lean <this file>

These names only feed the Retriever's `unknown identifier` suggestions; they are never
added to the search index (BM25 or dense), so the Phase 1.4 evaluation still holds.
Excluded here: the `Lean` namespace, private and internal names, deprecated aliases and
instances; auto-generated equation/congruence lemmas are dropped in Python.
-/

open Lean Meta Elab Command

#eval show CommandElabM Unit from do
  let some out ← IO.getEnv "FORMAL_MAS_NAMES_OUT"
    | throwError "set FORMAL_MAS_NAMES_OUT to the output path"
  let prefixes : List Name := [`Init, `Std, `Batteries]
  let env ← getEnv
  let h ← IO.FS.Handle.mk out .write
  let mut emitted : Nat := 0
  for (n, ci) in env.constants.map₁.toList do
    let .thmInfo _ := ci | continue
    let some idx := env.getModuleIdxFor? n | continue
    let mod := env.header.moduleNames[idx.toNat]!
    unless prefixes.any (·.isPrefixOf mod) do continue
    if (`Lean).isPrefixOf n || isPrivateName n || n.isInternalDetail
        || Linter.isDeprecated env n || isInstanceCore env n then
      continue
    h.putStrLn <| Json.compress <| Json.mkObj [
      ("name", toJson n.toString), ("module", toJson mod.toString)]
    emitted := emitted + 1
  h.flush
  logInfo m!"extract_names: emitted {emitted}"
