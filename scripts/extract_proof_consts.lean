import Mathlib

/-!
Dump, for every public theorem in the given Mathlib module prefixes, the constants used by
its compiled proof term, as JSON Lines (Phase 1.2: gold lemmas of the reference set Q).

Run from `lean_project/` (done by `scripts/build_reference_set.py`):

    FORMAL_MAS_CONSTS_OUT=<file> FORMAL_MAS_MODULE_PREFIXES=Mathlib.Algebra,Mathlib.NumberTheory \
      lake env lean <this file>

Internal auxiliary constants (`_proof_i`, `_simp_i`, `match_i`...) and private helpers are
expanded recursively so that lemmas used inside them are attributed to the theorem.
Each line has `name`, `module`, `range` (whole declaration) and `sel` (its name), both
`[line, col, end_line, end_col]`, and `consts`.
-/

open Lean Meta Elab Command

/-- Collect `n` and, through internal/private auxiliaries, everything they use. -/
partial def collect (env : Environment) (n : Name) : StateM NameSet Unit := do
  if (← get).contains n then return
  modify (·.insert n)
  if n.isInternalDetail || isPrivateName n then
    if let some ci := env.find? n then
      if let some v := ci.value? (allowOpaque := true) then
        for c in v.getUsedConstants do collect env c

def rangeJson (r : DeclarationRange) : Json :=
  toJson [r.pos.line, r.pos.column, r.endPos.line, r.endPos.column]

#eval show CommandElabM Unit from do
  let some out ← IO.getEnv "FORMAL_MAS_CONSTS_OUT"
    | throwError "set FORMAL_MAS_CONSTS_OUT to the output path"
  let prefixes := ((← IO.getEnv "FORMAL_MAS_MODULE_PREFIXES").getD "Mathlib").splitOn ","
    |>.map String.toName
  let env ← getEnv
  let h ← IO.FS.Handle.mk out .write
  let mut emitted : Nat := 0
  for (n, ci) in env.constants.map₁.toList do
    let .thmInfo ti := ci | continue
    if isPrivateName n || n.isInternalDetail then continue
    let some idx := env.getModuleIdxFor? n | continue
    let mod := env.header.moduleNames[idx.toNat]!
    unless prefixes.any (·.isPrefixOf mod) do continue
    let ((), used) := (ti.value.getUsedConstants.forM (collect env)).run {}
    let consts := used.toList.filter fun c => !c.isInternalDetail && !isPrivateName c && c != n
    let (range, sel) : Json × Json := match ← findDeclarationRanges? n with
      | some r => (rangeJson r.range, rangeJson r.selectionRange)
      | none => (Json.null, Json.null)
    h.putStrLn <| Json.compress <| Json.mkObj [
      ("name", toJson n.toString), ("module", toJson mod.toString), ("range", range),
      ("sel", sel), ("consts", toJson (consts.map (·.toString)))]
    emitted := emitted + 1
  h.flush
  logInfo m!"extract_proof_consts: emitted {emitted}"
