# PlutusV3 Batch 6 Scripts (PV11)

## Existing Scripts

### Language 1.1.0 ✅
- `1.1.0/succeedingIndexArrayPolicyScriptV3_1_1_0.plutus`
- `1.1.0/succeedingLengthOfArrayPolicyScriptV3_1_1_0.plutus`
- `1.1.0/succeedingListToArrayPolicyScriptV3_1_1_0.plutus`

Generated from plutus-scripts-e2e PR #12 (`yura/add-array-builtin-scripts` branch).

## Missing Scripts

### Language 1.0.0
- `1.0.0/succeedingIndexArrayPolicyScriptV3_1_0_0.plutus`
- `1.0.0/succeedingLengthOfArrayPolicyScriptV3_1_0_0.plutus`
- `1.0.0/succeedingListToArrayPolicyScriptV3_1_0_0.plutus`

## Generation Instructions

In plutus-scripts-e2e repository:

1. Create `PlutusScripts/Array/V3_1_0_0.hs` targeting PlutusV3 + language 1.0.0
2. Update `app/Main.hs` to generate envelopes for these scripts
3. Run `cabal run envelopes` to generate `.plutus` files
4. Copy generated scripts to the `1.0.0/` subdirectory

The validator logic from `PlutusScripts/Array/Common.hs` should be reused.
