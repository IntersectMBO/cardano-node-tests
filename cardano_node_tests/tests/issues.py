from cardano_node_tests.utils import blockers

ledger_4198 = blockers.GH(
    issue=4198,
    repo="IntersectMBO/cardano-ledger",
    fixed_in="8.11.0",
    message="Conway: submit fails with invalid Plutus script",
)
