import Mathlib

/-!
Dump the public Mathlib theorems of the elaborated environment as JSON Lines.

Run from `lean_project/` (done by `scripts/build_index.py`):

    FORMAL_MAS_CORPUS_OUT=<file> [FORMAL_MAS_MODULE_PREFIX=Mathlib.Foo] lake env lean <this file>

Line 1 is `{"_meta": ...}`; every other line is one theorem with its raw signature,
docstring, declaration range and `to_additive`/`to_dual` target flags. Hard filters that
need the environment (private, internal, deprecated, instances) are applied here; origin
classification and the remaining filters live in `formal_mas.retrieval.corpus`.
-/

open Lean Meta Elab Command

set_option pp.universes false
set_option format.width 100000

#eval show CommandElabM Unit from do
  let some out ← IO.getEnv "FORMAL_MAS_CORPUS_OUT"
    | throwError "set FORMAL_MAS_CORPUS_OUT to the output path"
  let pfx := ((← IO.getEnv "FORMAL_MAS_MODULE_PREFIX").getD "Mathlib").toName
  let env ← getEnv
  let opts ← getOptions
  let inScope (n : Name) : Option Name := do
    let idx ← env.getModuleIdxFor? n
    let mod := env.header.moduleNames[idx.toNat]!
    if pfx.isPrefixOf mod then some mod else none
  -- Pass 1: names produced by the translation attributes (targets of the maps).
  let mut additive : NameSet := {}
  let mut dual : NameSet := {}
  for (n, ci) in env.constants.map₁.toList do
    let .thmInfo _ := ci | continue
    if let some t := Mathlib.Tactic.ToAdditive.translations.find? env n then
      additive := additive.insert t.translation
    if let some t := Mathlib.Tactic.ToDual.translations.find? env n then
      dual := dual.insert t.translation
  -- Pass 2: emit candidates.
  let h ← IO.FS.Handle.mk out .write
  h.putStrLn <| Json.compress <| Json.mkObj [("_meta", Json.mkObj [
    ("lean_version", toJson Lean.versionString),
    ("module_prefix", toJson pfx.toString),
    ("pp_function", toJson "Lean.PrettyPrinter.ppSignature"),
    ("pp_options", Json.mkObj [
      ("pp.universes", toJson (pp.universes.get opts)),
      ("format.width", toJson (Std.Format.format.width.get opts))])])]
  let mut emitted : Nat := 0
  let mut skipped : Nat := 0
  let mut failed : Nat := 0
  for (n, ci) in env.constants.map₁.toList do
    let .thmInfo _ := ci | continue
    let some mod := inScope n | continue
    if isPrivateName n || n.isInternalDetail || Linter.isDeprecated env n
        || isInstanceCore env n then
      skipped := skipped + 1
      continue
    let sig? ← liftTermElabM do
      try return some (toString (← PrettyPrinter.ppSignature n).fmt)
      catch _ => return none
    let some sig := sig? | failed := failed + 1; continue
    let doc ← findDocString? env n
    let range : Json := match ← findDeclarationRanges? n with
      | some r => toJson [r.selectionRange.pos.line, r.selectionRange.pos.column,
                          r.selectionRange.endPos.line, r.selectionRange.endPos.column]
      | none => Json.null
    h.putStrLn <| Json.compress <| Json.mkObj [
      ("name", toJson n.toString), ("module", toJson mod.toString),
      ("signature_raw", toJson sig), ("docstring", toJson doc), ("range", range),
      ("to_additive", toJson (additive.contains n)), ("to_dual", toJson (dual.contains n))]
    emitted := emitted + 1
    if emitted % 20000 == 0 then IO.eprintln s!"extract_corpus: {emitted} emitted"
  h.flush
  logInfo m!"extract_corpus: emitted {emitted}, skipped {skipped}, pp failures {failed}"
