# PlutusV2 Batch 6 Scripts (PV11)

## Missing Scripts

The following PlutusV2 array builtin scripts need to be generated in [plutus-scripts-e2e](https://github.com/mkoura/plutus-scripts-e2e):

### Language 1.0.0
- `1.0.0/succeedingIndexArrayPolicyScriptV2_1_0_0.plutus`
- `1.0.0/succeedingLengthOfArrayPolicyScriptV2_1_0_0.plutus`
- `1.0.0/succeedingListToArrayPolicyScriptV2_1_0_0.plutus`

### Language 1.1.0
- `1.1.0/succeedingIndexArrayPolicyScriptV2_1_1_0.plutus`
- `1.1.0/succeedingLengthOfArrayPolicyScriptV2_1_1_0.plutus`
- `1.1.0/succeedingListToArrayPolicyScriptV2_1_1_0.plutus`

## Generation Instructions

In plutus-scripts-e2e repository:

1. Create `PlutusScripts/Array/V2_1_0_0.hs` targeting PlutusV2 + language 1.0.0
2. Create `PlutusScripts/Array/V2_1_1_0.hs` targeting PlutusV2 + language 1.1.0
3. Update `app/Main.hs` to generate envelopes for these scripts
4. Run `cabal run envelopes` to generate `.plutus` files
5. Copy generated scripts to this directory

The validator logic from `PlutusScripts/Array/Common.hs` should be reused across all versions.
