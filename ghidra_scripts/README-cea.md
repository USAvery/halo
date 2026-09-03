# CEA Ghidra scripts

These scripts support the Halo CE Anniversary (CEA) PDB/type corpus workflow. Run them from Ghidra's Script Manager with a program containing the Halo Xbox image where applicable. Paths below are the defaults compiled into each script; Ghidra script arguments are positional and should be supplied as one argument string as supported by the local Ghidra setup.

## Scripts

### `CeaPdbExtract.java`

Extracts Halo `.obj` procedure symbols and source-line ranges from a PDB without modifying the open program.

- **Overrides:** `getScriptArgs()[0]` = PDB path; `[1]` = output TSV path. Empty values retain the defaults.
- **Default input:** `C:\Users\stian\Downloads\Halo_1_Combat_Evolved_Anniversary_(Jun_24,_2011)\en_us\HCEX_Release.pdb`
- **Default output:** `G:\dev\halo\artifacts\ghidra_groom\cea_corpus\cea_procs_raw.tsv`
- **Artifact:** tab-separated module/procedure/segment/offset/length/source-file/line-range records, including totals printed in the Ghidra console.
- **Remaining non-portable paths:** both default Windows paths above; the extractor also filters PDB module names containing `\\halo\\` and ending in `.obj`.

### `CeaTypeExtract.java`

Extracts named and anonymous structs, unions, enums, fields, and type references from a PDB's type information without modifying the open program.

- **Overrides:** `getScriptArgs()[0]` = PDB path; `[1]` = output JSON path. Empty values retain the defaults.
- **Default input:** `C:\Users\stian\Downloads\Halo_1_Combat_Evolved_Anniversary_(Jun_24,_2011)\en_us\HCEX.pdb`
- **Default output:** `G:\dev\halo\artifacts\ghidra_groom\type_corpus\cea_debug_types_raw.json`
- **Artifact:** JSON array of composite and enum type records, including sizes, fields, offsets, type indices, and enum members.
- **Remaining non-portable paths:** both default Windows paths above.

### `CeaAssertLines.java`

Reads assert/error call sites from the open program and recovers source file and line arguments from nearby pushes. It is read-only.

- **Overrides:** none; this script does **not** call `getScriptArgs()`.
- **Hardcoded output:** `G:\dev\halo\artifacts\ghidra_groom\cea_corpus\our_assert_lines_raw.tsv`
- **Artifact:** tab-separated containing function entry/name, source file, line, callsite, and assert kind.
- **Remaining non-portable paths:** the hardcoded output path. The source-string matcher also expects `c:\...*.c` strings.

### `CeaDumpNames.java`

Dumps every function entry and current name from the open program so corpus matching can avoid collisions. It is read-only.

- **Overrides:** none; this script does **not** call `getScriptArgs()`.
- **Hardcoded output:** `G:\dev\halo\artifacts\ghidra_groom\cea_corpus\ghidra_func_names.tsv`
- **Artifact:** tab-separated `address`/`name` rows and function counts printed in the Ghidra console.
- **Remaining non-portable paths:** the hardcoded output path.

### `CeaApplyRenames.java`

Applies or dry-runs CEA-PDB function renames from a TSV, checking the current function name and rejecting missing, mismatched, or colliding targets.

- **Overrides:** `getScriptArgs()[0]` = input rename TSV; `[1]` = literal `APPLY` for changes. Omit the second argument for the default dry run. The script requires at least the first argument.
- **Hardcoded output:** `G:\dev\halo\artifacts\ghidra_groom\ghidra_rename_result.txt`
- **Artifact:** result log with mode, rename counts, and per-entry mismatch/collision/not-found details. In `APPLY` mode it also adds CEA plate comments.
- **Remaining non-portable paths:** the hardcoded result path; the rename TSV is portable only when supplied explicitly.

### `CeaImportTypes.java`

Imports verified CEA datum/math structures from a flat TSV into the `/halo/cea` Ghidra data-type category with packing disabled.

- **Overrides:** `getScriptArgs()[0]` = input type TSV; omit it to use the default.
- **Default input:** `G:\dev\halo\artifacts\ghidra_groom\type_corpus\import_work.tsv`
- **Hardcoded output:** `G:\dev\halo\artifacts\ghidra_groom\type_corpus\import_result.txt`
- **Artifacts:** imported/replaced data types under `/halo/cea`, plus an import log with type/field counts and failures.
- **Remaining non-portable paths:** both default input and result paths above; the category path `/halo/cea` is also fixed.

### `CeaImportEnums.java`

Imports CEA game enums from a flat TSV into the `/halo/cea` Ghidra data-type category, skipping name collisions.

- **Overrides:** `getScriptArgs()[0]` = input enum TSV; omit it to use the default.
- **Default input:** `G:\dev\halo\artifacts\ghidra_groom\type_corpus\enum_import_work.tsv`
- **Hardcoded output:** `G:\dev\halo\artifacts\ghidra_groom\type_corpus\enum_import_result.txt`
- **Artifacts:** newly created enum data types under `/halo/cea`, plus an import log with enum/member counts and skipped collisions.
- **Remaining non-portable paths:** both default input and result paths above; the category path `/halo/cea` is also fixed.

## Important corpus distinction

`CeaPdbExtract` and `CeaTypeExtract` consume HCEX PDB files and produce intermediate CEA symbol/type artifacts. Those HCEX PDB outputs are **not** the `halocea` decompiled corpus; do not treat the generated procedure or type records as the decompiled source corpus itself.
