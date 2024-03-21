"""Conway User Stories."""

from cardano_node_tests.utils import requirements as r

__group = r.GroupsKnown.CHANG_US

# https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/01-cip1694.md
cip001a = r.Req(id="CIP001a", group=__group)
cip001b = r.Req(id="CIP001b", group=__group)
cip002 = r.Req(id="CIP002", group=__group)
cip003 = r.Req(id="CIP003", group=__group)
cip004 = r.Req(id="CIP004", group=__group)
cip005 = r.Req(id="CIP005", group=__group)
cip006 = r.Req(id="CIP006", group=__group)
cip007 = r.Req(id="CIP007", group=__group)
cip008 = r.Req(id="CIP008", group=__group)
cip009 = r.Req(id="CIP009", group=__group)
cip010 = r.Req(id="CIP010", group=__group)
cip011 = r.Req(id="CIP011", group=__group)
cip012 = r.Req(id="CIP012", group=__group)
cip013 = r.Req(id="CIP013", group=__group)
cip014 = r.Req(id="CIP014", group=__group)
cip016 = r.Req(id="CIP016", group=__group)
cip017 = r.Req(id="CIP017", group=__group)
cip018 = r.Req(id="CIP017", group=__group)
cip020_01 = r.Req(id="intCIP020-01", group=__group)  # verification key
cip020_02 = r.Req(id="intCIP020-02", group=__group)  # Native script
cip020_03 = r.Req(id="intCIP020-03", group=__group)  # Plutus script
cip021 = r.Req(id="CIP021", group=__group)
cip022 = r.Req(id="CIP022", group=__group)
cip023 = r.Req(id="CIP023", group=__group)
cip024 = r.Req(id="CIP024", group=__group)
cip025 = r.Req(id="CIP025", group=__group)
cip029 = r.Req(id="CIP029", group=__group)
cip030en = r.Req(id="intCIP030en", group=__group)  # enacted
cip030ex = r.Req(id="intCIP030ex", group=__group)  # expired
cip031a_01 = r.Req(id="intCIP031a-01", group=__group)  # committee
cip031a_02 = r.Req(id="intCIP031a-02", group=__group)  # constitution
cip031a_03 = r.Req(id="intCIP031a-03", group=__group)  # info
cip031a_04 = r.Req(id="intCIP031a-04", group=__group)  # no confidence
cip031a_05 = r.Req(id="intCIP031a-05", group=__group)  # pparam update
cip031a_06 = r.Req(id="intCIP031a-06", group=__group)  # treasury withdrawal
cip031a_07 = r.Req(id="intCIP031a-07", group=__group)  # hard-fork
cip031b = r.Req(id="CIP031b", group=__group)
cip031c_01 = r.Req(id="intCIP031c-01", group=__group)  # anchor
cip031c_02 = r.Req(id="intCIP031c-02", group=__group)  # script hash
cip031e = r.Req(id="CIP031e", group=__group)
cip031f = r.Req(id="CIP031f", group=__group)
cip032en = r.Req(id="intCIP032en", group=__group)  # enacted
cip032ex = r.Req(id="intCIP032ex", group=__group)  # expired
cip033 = r.Req(id="CIP033", group=__group)
cip034en = r.Req(id="intCIP034en", group=__group)  # enacted
cip034ex = r.Req(id="intCIP034ex", group=__group)  # expired
cip037 = r.Req(id="CIP037", group=__group)
cip038_01 = r.Req(id="intCIP038-01", group=__group)  # committee
cip038_02 = r.Req(id="intCIP038-02", group=__group)  # constitution
cip038_03 = r.Req(id="intCIP038-03", group=__group)  # no confidence
cip038_04 = r.Req(id="intCIP038-04", group=__group)  # pparam update
cip038_05 = r.Req(id="intCIP038-05", group=__group)  # info
cip038_06 = r.Req(id="intCIP038-06", group=__group)  # treasury withdrawal
cip038_07 = r.Req(id="intCIP038-07", group=__group)  # hard-fork
cip039 = r.Req(id="CIP039", group=__group)
cip040 = r.Req(id="CIP040", group=__group)
cip041 = r.Req(id="CIP041", group=__group)
cip042 = r.Req(id="CIP042", group=__group)
cip044 = r.Req(id="CIP044", group=__group)
cip045 = r.Req(id="CIP045", group=__group)
cip046 = r.Req(id="CIP046", group=__group)
cip047 = r.Req(id="CIP047", group=__group)
cip048 = r.Req(id="CIP048", group=__group)
cip049 = r.Req(id="CIP049", group=__group)
cip050 = r.Req(id="CIP050", group=__group)
cip051 = r.Req(id="CIP051", group=__group)
cip052 = r.Req(id="CIP052", group=__group)
cip053 = r.Req(id="CIP053", group=__group)
cip054_01 = r.Req(id="intCIP054-01", group=__group)  # pparam update
cip054_02 = r.Req(id="intCIP054-02", group=__group)  # committee
cip054_03 = r.Req(id="intCIP054-03", group=__group)  # constitution
cip054_04 = r.Req(id="intCIP054-04", group=__group)  # no confidence
cip054_05 = r.Req(id="intCIP054-05", group=__group)  # treasury withdrawal
cip054_06 = r.Req(id="intCIP054-06", group=__group)  # info
cip054_07 = r.Req(id="intCIP054-07", group=__group)  # hard-fork
cip057 = r.Req(id="CIP057", group=__group)
cip058 = r.Req(id="CIP058", group=__group)
cip059 = r.Req(id="CIP059", group=__group)
cip060 = r.Req(id="CIP060", group=__group)
cip064_01 = r.Req(id="intCIP064-01", group=__group)  # DRep unreg stake not 'No'
cip064_02 = r.Req(id="intCIP064-02", group=__group)  # SPO unreg stake not 'No'
cip064_03 = r.Req(id="intCIP064-03", group=__group)  # DRep unreg stake not 'Yes'
cip064_04 = r.Req(id="intCIP064-04", group=__group)  # SPO unreg stake not 'Yes'
cip065 = r.Req(id="CIP065", group=__group)
cip067 = r.Req(id="CIP067", group=__group)
cip068 = r.Req(id="CIP068", group=__group)
cip069en = r.Req(id="intCIP069en", group=__group)  # enacted
cip069ex = r.Req(id="intCIP069ex", group=__group)  # expired
cip070 = r.Req(id="CIP070", group=__group)
cip073_01 = r.Req(id="intCIP073-01", group=__group)  # actions staged for enactment
cip073_02 = r.Req(id="intCIP073-02", group=__group)  # total and percentage of Yes/... stake
cip073_03 = r.Req(id="intCIP073-03", group=__group)  # current constitutional committee
cip073_04 = r.Req(id="intCIP073-04", group=__group)  # constitution hash
cip074 = r.Req(id="CIP074", group=__group)
cip075 = r.Req(id="CIP075", group=__group)
cip076 = r.Req(id="CIP076", group=__group)
cip077 = r.Req(id="CIP077", group=__group)
cip078 = r.Req(id="CIP078", group=__group)

# https://github.com/IntersectMBO/cardano-test-plans/blob/main/docs/user-stories/02-cardano-cli.md
cli001 = r.Req(id="CLI001", group=__group)
cli002 = r.Req(id="CLI002", group=__group)
cli003 = r.Req(id="CLI003", group=__group)
cli004 = r.Req(id="CLI004", group=__group)
cli005 = r.Req(id="CLI005", group=__group)
cli006 = r.Req(id="CLI006", group=__group)
cli007 = r.Req(id="CLI007", group=__group)
cli008 = r.Req(id="CLI008", group=__group)
cli009 = r.Req(id="CLI009", group=__group)
cli010 = r.Req(id="CLI010", group=__group)
cli011 = r.Req(id="CLI011", group=__group)
cli012 = r.Req(id="CLI012", group=__group)
cli013 = r.Req(id="CLI013", group=__group)
cli014 = r.Req(id="CLI014", group=__group)
cli015 = r.Req(id="CLI015", group=__group)
cli016 = r.Req(id="CLI016", group=__group)
cli017 = r.Req(id="CLI017", group=__group)
cli018 = r.Req(id="CLI018", group=__group)
cli020 = r.Req(id="CLI020", group=__group)
cli021 = r.Req(id="CLI021", group=__group)
cli022 = r.Req(id="CLI022", group=__group)
cli023 = r.Req(id="CLI023", group=__group)
cli024 = r.Req(id="CLI024", group=__group)
cli025 = r.Req(id="CLI025", group=__group)
cli026 = r.Req(id="CLI026", group=__group)
cli027 = r.Req(id="CLI027", group=__group)
cli028 = r.Req(id="CLI028", group=__group)
cli029 = r.Req(id="CLI029", group=__group)
cli030 = r.Req(id="CLI030", group=__group)
cli031 = r.Req(id="CLI031", group=__group)
cli032 = r.Req(id="CLI032", group=__group)
cli033 = r.Req(id="CLI033", group=__group)
cli034 = r.Req(id="CLI034", group=__group)
cli035 = r.Req(id="CLI035", group=__group)
cli036 = r.Req(id="CLI036", group=__group)
