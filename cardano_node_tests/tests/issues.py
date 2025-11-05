from cardano_node_tests.utils import blockers

api_269 = blockers.GH(
    issue=269,
    repo="IntersectMBO/cardano-api",
    message="Broken `nextEpochEligibleLeadershipSlots`.",
)
api_484 = blockers.GH(
    issue=484,
    repo="IntersectMBO/cardano-api",
    fixed_in="10.7.0",  # Unknown yet, will be fixed/changed sometime in the future
    message="Repeated certificates stripped from Conway transaction.",
)
api_829 = blockers.GH(
    issue=829,
    repo="IntersectMBO/cardano-api",
    fixed_in="10.5.0.0",
    message="Wrong autobalancing when there's no change.",
)

cli_49 = blockers.GH(
    issue=49,
    fixed_in="9.4.1.1",  # Fixed in version after 9.4.1.0
    repo="IntersectMBO/cardano-cli",
    message="Not sending pings.",
)
cli_268 = blockers.GH(
    issue=268, repo="IntersectMBO/cardano-cli", message="Internal query mismatch."
)
cli_297 = blockers.GH(
    issue=297,
    repo="IntersectMBO/cardano-cli",
    message="Cannot delegate Plutus stake address.",
)
cli_299 = blockers.GH(
    issue=299,
    repo="IntersectMBO/cardano-cli",
    message="Cannot de-register Plutus stake address.",
)
cli_614 = blockers.GH(
    issue=614,
    repo="IntersectMBO/cardano-cli",
    fixed_in="9.3.0.1",  # Unknown yet, will be fixed sometime in the future
    message="Overspent budget when minting assets and autobalancing a transaction.",
)
cli_650 = blockers.GH(
    issue=650,
    repo="IntersectMBO/cardano-cli",
    fixed_in="8.22.0.1",  # Unknown yet, will be fixed sometime in the future
    message="Plutus cost too low.",
)
cli_715 = blockers.GH(
    issue=715,
    repo="IntersectMBO/cardano-cli",
    fixed_in="8.22.0.1",  # Fixed in a release after 8.22.0.0
    message="Option `--reference-script-size` required.",
)
cli_768 = blockers.GH(
    issue=768,
    repo="IntersectMBO/cardano-cli",
    fixed_in="8.23.1.1",  # Fixed in a release after 8.23.1.0
    message="Option `--fee` not required.",
)
cli_796 = blockers.GH(
    issue=796,
    repo="IntersectMBO/cardano-cli",
    fixed_in="8.24.0.1",  # Fixed in a release after 8.24.0.0
    message="Option `--fee` not required.",
)
cli_799 = blockers.GH(
    issue=799,
    repo="IntersectMBO/cardano-cli",
    fixed_in="9.1.0.0",  # Found in 8.24.0.0
    message="Conway era fields shown in Babbage Tx.",
)
cli_800 = blockers.GH(
    issue=800,
    repo="IntersectMBO/cardano-cli",
    fixed_in="8.24.0.1",  # Fixed in a release after 8.24.0.0
    message="Datum not checked for PlutusV1 and PlutusV2 spending scripts.",
)
cli_860 = blockers.GH(
    issue=860,
    repo="IntersectMBO/cardano-cli",
    fixed_in="9.10.0.0",  # Fixed in some release after 9.2.1.0
    message="Negative pparam proposal values overflow to positive.",
)
cli_904 = blockers.GH(
    issue=904,
    repo="IntersectMBO/cardano-cli",
    fixed_in="10.1.2.1",  # Fixed in some release after 10.1.2.0
    message="Negative pparam proposal values overflow to positive.",
)
cli_942 = blockers.GH(
    issue=942,
    repo="IntersectMBO/cardano-cli",
    fixed_in="10.0.0.1",  # Fixed in some release after 10.0.0.0
    message="build command doesn't balance key deposit.",
)
cli_953 = blockers.GH(
    issue=953,
    repo="IntersectMBO/cardano-cli",
    fixed_in="10.1.2.0",  # Fixed in some release after 10.1.1.0
    message="query tip is not a top level command.",
)
cli_1023 = blockers.GH(
    issue=1023,
    repo="IntersectMBO/cardano-cli",
    fixed_in="10.3.0.1",  # Fixed in release following 10.3.0.0
    message="Plutus cost too low.",
)
cli_1199 = blockers.GH(
    issue=1199,
    repo="IntersectMBO/cardano-cli",
    fixed_in="10.10.0.1",  # Fixed in release after 10.10.0.0
    message="`build-estimate` fails to balance tx with no txouts.",
)

consensus_973 = blockers.GH(
    issue=973,
    repo="IntersectMBO/ouroboros-consensus",
    fixed_in="8.9.1",
    message="Tx with invalid Plutus script stuck in mempool.",
)
consensus_947 = blockers.GH(
    issue=947,
    repo="IntersectMBO/ouroboros-consensus",
    fixed_in="8.9.0",
    message="Submit fails with invalid Plutus script.",
)

dbsync_1363 = blockers.GH(
    issue=1363,
    repo="IntersectMBO/cardano-db-sync",
    message="Blocks count don't match between tables.",
)
dbsync_1825 = blockers.GH(
    issue=1825,
    repo="IntersectMBO/cardano-db-sync",
    message="Wrong PlutusV2 script cost when the same script is used twice.",
)

ledger_3731 = blockers.GH(
    issue=3731,
    fixed_in="8.10.0",
    repo="IntersectMBO/cardano-ledger",
    message="base64 encoded binary script.",
)
ledger_3890 = blockers.GH(
    issue=3890,
    repo="IntersectMBO/cardano-ledger",
    message="DRepRegistration certificate must require a witness.",
)
ledger_3979 = blockers.GH(
    issue=3979,
    repo="IntersectMBO/cardano-ledger",
    message="Only single action got removed.",
)
ledger_4001 = blockers.GH(
    issue=4001,
    repo="IntersectMBO/cardano-ledger",
    message="Newly elected CC members are removed.",
)
ledger_4198 = blockers.GH(
    issue=4198,
    repo="IntersectMBO/cardano-ledger",
    fixed_in="8.11.0",
    message="Conway: submit fails with invalid Plutus script.",
)
ledger_4204 = blockers.GH(
    issue=4204,
    repo="IntersectMBO/cardano-ledger",
    fixed_in="8.11.0",
    message="Resigned CC members can approve actions.",
)
ledger_4346 = blockers.GH(
    issue=4346,
    repo="IntersectMBO/cardano-ledger",
    fixed_in="8.12.0",  # Unknown yet, will be fixed/changed sometime in the future
    message="Inactive DRep expiry gets incremented.",
)
ledger_4349 = blockers.GH(
    issue=4349,
    repo="IntersectMBO/cardano-ledger",
    fixed_in="8.12.0",  # Unknown yet, will be fixed/changed sometime in the future
    message="Inconsistent listing of DRep expiry.",
)
ledger_4772 = blockers.GH(
    issue=4772,
    repo="IntersectMBO/cardano-ledger",
    fixed_in="10.1.3.0",  # Unknown yet, will be fixed/changed sometime in the future
    message="Delegation to DRep2 removed after retirement of DRep1.",
)
ledger_5365 = blockers.GH(
    issue=5365,
    repo="IntersectMBO/cardano-ledger",
    fixed_in="10.6.0.0",
    message="queryPoolState returns current pool params instead of the future ones.",
)

node_3788 = blockers.GH(
    issue=3788,
    fixed_in="8.0.0",
    message="Possible to create an op cert with a negative value for kes-period.",
)
node_2461 = blockers.GH(issue=2461, message="`query protocol-state --out-file` dumps binary data.")
node_3835 = blockers.GH(issue=3835, fixed_in="8.0.0", message="Assemble Tx with no signatures")
node_3859 = blockers.GH(issue=3859, message="Expected JSON, got CBOR.")
node_4002 = blockers.GH(issue=4002, message="'PastHorizon' in `query leadership-schedule`.")
node_4058 = blockers.GH(
    issue=4058,
    fixed_in="8.0.0",
    message="`transaction build` requires protocol params.",
)
node_4114 = blockers.GH(issue=4114, message="Undetected invalid counter and certificate.")
node_4235 = blockers.GH(
    issue=4235, fixed_in="8.0.0", message="Not possible to use process substitution."
)
node_4261 = blockers.GH(issue=4261, fixed_in="8.0.0", message="Reported 'SimpleScriptV2'.")
node_4297 = blockers.GH(
    issue=4297,
    message="`transaction build` min required UTxO calculation is broken.",
)
node_4396 = blockers.GH(issue=4396, message="Returned null for `qKesKesKeyExpiry` metric.")
node_4424 = blockers.GH(issue=4424, message="Inconsistent handling of Babbage-only features.")
node_4433 = blockers.GH(
    issue=4433,
    message="Datum bytes in db-sync doesn't correspond to the original datum.",
)
node_4488 = blockers.GH(
    issue=4488, message="PlutusDebug doesn't return the evaluation error from plutus."
)
node_4591 = blockers.GH(issue=4591, message="Transaction feature not supported.")
node_4752 = blockers.GH(issue=4752, message="`FeeTooSmallUTxO` error.")
node_4744 = blockers.GH(issue=4744, message="`IncorrectTotalCollateralField` error.")
node_4863 = blockers.GH(issue=4863, fixed_in="8.0.0", message="UINT64 overflow.")
node_4895 = blockers.GH(issue=4895, message="Unexpected values for total stake.")
node_4914 = blockers.GH(issue=4914, message="Invalid non-extended-key.")
node_5182 = blockers.GH(issue=5182, fixed_in="8.7.0", message="'Prelude.!!' in error message.")
node_5199 = blockers.GH(issue=5199, message="`CARDANO_NODE_SOCKET_PATH` needed.")
node_5245 = blockers.GH(
    issue=5245,
    fixed_in="8.2.0",
    message="`MuxError MuxBearerClosed` error.",
)
node_5324 = blockers.GH(issue=5324, fixed_in="8.1.1", message="`UnknownVersionInRsp` error.")

plutus_apps_583 = blockers.GH(
    issue=583, repo="IntersectMBO/plutus-apps", message="`DeserialiseFailure` error."
)
plutus_apps_1078 = blockers.GH(
    issue=1078, repo="IntersectMBO/plutus-apps", message="`TextEnvelopeTypeError` error."
)
plutus_apps_1107 = blockers.GH(
    issue=1107,
    repo="IntersectMBO/plutus-apps",
    message="PlutusScriptV1 custom redeemer.",
)
