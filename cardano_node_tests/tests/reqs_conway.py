"""Conway User Stories."""

import os

from cardano_node_tests.utils import requirements

__HAS_DBSYNC = bool(os.environ.get("DBSYNC_SCHEMA_DIR"))


def __r(id: str) -> requirements.Req:
    return requirements.Req(id=id, group=requirements.GroupsKnown.CHANG_US)


# db-sync related requirement
def __dr(id: str) -> requirements.Req:
    return requirements.Req(id=id, group=requirements.GroupsKnown.CHANG_US, enabled=__HAS_DBSYNC)


# https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md
cip001a = __r("CIP001a")
cip001b = __r("CIP001b")
cip002 = __r("CIP002")
cip003 = __r("CIP003")
cip004 = __r("CIP004")
cip005 = __r("CIP005")
cip006 = __r("CIP006")
cip007 = __r("CIP007")
cip008 = __r("CIP008")
cip009 = __r("CIP009")
cip010 = __r("CIP010")
cip011 = __r("CIP011")
cip012 = __r("CIP012")
cip013 = __r("CIP013")
cip014 = __r("CIP014")
cip015 = __r("CIP015")
cip016 = __r("CIP016")
cip017 = __r("CIP017")
cip018 = __r("CIP018")
cip019 = __r("CIP019")
cip020_01 = __r("intCIP020-01")  # verification key
cip020_02 = __r("intCIP020-02")  # Native script
cip020_03 = __r("intCIP020-03")  # Plutus script
cip021 = __r("CIP021")
cip022 = __r("CIP022")
cip023 = __r("CIP023")
cip024 = __r("CIP024")
cip025 = __r("CIP025")
cip026_01 = __r("intCIP026-01")  # Committee disallowed
cip026_02 = __r("intCIP026-02")  # Constitution disallowed
cip026_03 = __r("intCIP026-03")  # Withdrawal disallowed
cip026_04 = __r("intCIP026-04")  # DRep voting disallowed
cip027 = __r("CIP027")
cip028 = __r("CIP028")
cip029 = __r("CIP029")
cip030en = __r("intCIP030en")  # enacted
cip030ex = __r("intCIP030ex")  # expired
cip031a_01 = __r("intCIP031a-01")  # committee
cip031a_02 = __r("intCIP031a-02")  # constitution
cip031a_03 = __r("intCIP031a-03")  # info
cip031a_04 = __r("intCIP031a-04")  # no confidence
cip031a_05 = __r("intCIP031a-05")  # pparam update
cip031a_06 = __r("intCIP031a-06")  # treasury withdrawal
cip031a_07 = __r("intCIP031a-07")  # hard-fork
cip031b = __r("CIP031b")
cip031c_01 = __r("intCIP031c-01")  # anchor
cip031c_02 = __r("intCIP031c-02")  # script hash
cip031d = __r("CIP031d")
cip031e = __r("CIP031e")
cip031f = __r("CIP031f")
cip032en = __r("intCIP032en")  # enacted
cip032ex = __r("intCIP032ex")  # expired
cip033 = __r("CIP033")
cip034en = __r("intCIP034en")  # enacted
cip034ex = __r("intCIP034ex")  # expired
cip036 = __r("CIP036")  # constitution script
cip037 = __r("CIP037")
cip038_01 = __r("intCIP038-01")  # committee
cip038_02 = __r("intCIP038-02")  # constitution
cip038_03 = __r("intCIP038-03")  # no confidence
cip038_04 = __r("intCIP038-04")  # pparam update
cip038_05 = __r("intCIP038-05")  # info
cip038_06 = __r("intCIP038-06")  # treasury withdrawal
cip038_07 = __r("intCIP038-07")  # hard-fork
cip039 = __r("CIP039")
cip040 = __r("CIP040")
cip041 = __r("CIP041")
cip042 = __r("CIP042")
cip043_01 = __r("intCIP043-01")  # bootstrap phase where DReps don't vote
cip043_02 = __r("intCIP043-02")  # hard-fork initiation with DRep votes
cip044 = __r("CIP044")
cip045 = __r("CIP045")
cip046 = __r("CIP046")
cip047 = __r("CIP047")
cip048 = __r("CIP048")
cip049 = __r("CIP049")
cip050 = __r("CIP050")
cip051 = __r("CIP051")
cip052 = __r("CIP052")
cip053 = __r("CIP053")
cip054_01 = __r("intCIP054-01")  # pparam update
cip054_02 = __r("intCIP054-02")  # committee
cip054_03 = __r("intCIP054-03")  # constitution
cip054_04 = __r("intCIP054-04")  # no confidence
cip054_05 = __r("intCIP054-05")  # treasury withdrawal
cip054_06 = __r("intCIP054-06")  # info
cip054_07 = __r("intCIP054-07")  # hard-fork
cip056 = __r("CIP056")
cip057 = __r("CIP057")
cip058 = __r("CIP058")
cip059 = __r("CIP059")
cip060 = __r("CIP060")
cip061_01 = __r("intCIP061-01")  # SPO 'Yes' + 'Abstain' votes
cip061_02 = __r("intCIP061-02")  # SPO 'No' + 'Abstain' votes
cip061_03 = __r("intCIP061-03")  # DRep 'Yes' + 'Abstain' votes
cip061_04 = __r("intCIP061-04")  # DRep 'No' + 'Abstain' votes
cip062_01 = __r("intCIP062-01")  # CC 'Yes' votes over threshold approve
cip062_02 = __r("intCIP062-02")  # Majority of CC 'No' votes reject
cip064_01 = __r("intCIP064-01")  # DRep unreg stake not 'No'
cip064_02 = __r("intCIP064-02")  # SPO unreg stake not 'No'
cip064_03 = __r("intCIP064-03")  # DRep unreg stake not 'Yes'
cip064_04 = __r("intCIP064-04")  # SPO unreg stake not 'Yes'
cip066 = __r("CIP066")
cip065 = __r("CIP065")
cip067 = __r("CIP067")
cip068 = __r("CIP068")
cip069en = __r("intCIP069en")  # enacted
cip069ex = __r("intCIP069ex")  # expired
cip070 = __r("CIP070")
cip071 = __r("CIP071")
cip072 = __r("CIP072")
cip073_01 = __r("intCIP073-01")  # actions staged for enactment
cip073_02 = __r("intCIP073-02")  # total and percentage of Yes/... stake
cip073_03 = __r("intCIP073-03")  # current constitutional committee
cip073_04 = __r("intCIP073-04")  # constitution hash
cip074 = __r("CIP074")
cip075 = __r("CIP075")
cip076 = __r("CIP076")
cip077 = __r("CIP077")
cip078 = __r("CIP078")
cip079 = __r("CIP079")
cip080 = __dr("CIP080")
cip081 = __dr("CIP081")
cip082 = __dr("CIP082")
cip083 = __dr("CIP083")
cip084 = __dr("CIP084")
cip085 = __r("CIP085")
cip086 = __r("CIP086")
cip087 = __r("CIP087")
cip088 = __r("CIP088")
cip089 = __r("CIP089")
cip090 = __r("CIP090")

# https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md
cli001 = __r("CLI001")
cli002 = __r("CLI002")
cli003 = __r("CLI003")
cli004 = __r("CLI004")
cli005 = __r("CLI005")
cli006 = __r("CLI006")
cli007 = __r("CLI007")
cli008 = __r("CLI008")
cli009 = __r("CLI009")
cli010 = __r("CLI010")
cli011 = __r("CLI011")
cli012 = __r("CLI012")
cli013 = __r("CLI013")
cli014 = __r("CLI014")
cli015 = __r("CLI015")
cli016 = __r("CLI016")
cli017 = __r("CLI017")
cli018 = __r("CLI018")
cli019 = __r("CLI019")
cli020 = __r("CLI020")
cli021 = __r("CLI021")
cli022 = __r("CLI022")
cli023 = __r("CLI023")
cli024 = __r("CLI024")
cli025 = __r("CLI025")
cli026 = __r("CLI026")
cli027 = __r("CLI027")
cli028 = __r("CLI028")
cli029 = __r("CLI029")
cli030 = __r("CLI030")
cli031 = __r("CLI031")
cli032 = __r("CLI032")
cli033 = __r("CLI033")
cli034 = __r("CLI034")
cli035 = __r("CLI035")
cli036 = __r("CLI036")

# Guardrails user stories
# https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/inventory/07-governance-guardrails.md
gr001 = __r("GR001")
gr002 = __r("GR002")
gr003 = __r("GR003")
gr004 = __r("GR004")
gr005 = __r("GR005")
gr006 = __r("GR006")
gr007a = __r("GR007a")
gr007b = __r("GR007b")
gr008 = __r("GR008")
gr009a = __r("GR009a")
gr009b = __r("GR009b")
gr010a = __r("GR010a")
gr010b = __r("GR010b")
gr011 = __r("GR011")
gr012 = __r("GR012")
gr013 = __r("GR013")
gr014a = __r("GR014a")
gr014b = __r("GR014b")
gr014c = __r("GR014c")
gr014d = __r("GR014d")
gr014e = __r("GR014e")
gr015a = __r("GR015a")
gr015b = __r("GR015b")
gr015c = __r("GR015c")
gr015d = __r("GR015d")
gr015e = __r("GR015e")
gr015f = __r("GR015f")
gr015g = __r("GR015g")
gr015h = __r("GR015h")
gr015i = __r("GR015i")
gr015j = __r("GR015j")
gr016 = __r("GR016")
gr017 = __r("GR017")
gr018 = __r("GR018")
gr019 = __r("GR019")
gr020 = __r("GR020")
gr021 = __r("GR021")
gr022 = __r("GR022")
gr023 = __r("GR023")
gr024 = __r("GR024")
gr025 = __r("GR025")
gr026 = __r("GR026")
gr027 = __r("GR027")
gr028 = __r("GR028")
gr029 = __r("GR029")

# Internal Test Cases
int001 = __r("R10_1_4")
int002 = __r("R10_1_3")

# DB Sync Conway related tables
# https://github.com/IntersectMBO/cardano-db-sync/blob/master/doc/schema.md
db001 = __dr("drep_hash")
db002 = __dr("committee_hash")
db003 = __dr("delegation_vote")
db004 = __dr("committee_registration")
db005 = __dr("committee_de_registration")
db006 = __dr("drep_registration")
db007 = __dr("voting_anchor")
db008 = __dr("gov_action_proposal")
db009 = __dr("treasury_withdrawal")
db010 = __dr("committee")
db011 = __dr("committee_member")
db012 = __dr("constitution")
db013 = __dr("voting_procedure")
db014 = __dr("drep_distr")
db015 = __dr("off_chain_vote_data")
db016 = __dr("off_chain_vote_drep_data")
db017 = __dr("off_chain_vote_author")
db018 = __dr("off_chain_vote_reference")
db019 = __dr("off_chain_vote_data")
db020 = __dr("off_chain_vote_external_update")
db021 = __dr("off_chain_vote_fetch_error")
db022 = __dr("reward_rest")
db023 = __dr("param_proposal")  # new/updated fields
db024 = __dr("epoch_param")  # new/updated fields
db025_01 = __dr("int-epoch_state-01")  # related to committee
db025_02 = __dr("int-epoch_state-02")  # related to constitution
