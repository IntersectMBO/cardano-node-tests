from cardano_node_tests.cluster_management import cluster_getter
from cardano_node_tests.cluster_management import resources_management

ALL_POOLS = {"pool1", "pool2", "pool3", "pool4", "pool5"}


def test_sanitize4path():
    oneof = resources_management.OneOf(ALL_POOLS)
    lock_resources = ["$$res", oneof, "res1"]
    use_resources = ["res2", oneof, "res$&#@"]
    mark = cluster_getter.sanitize4path(
        "res-Foo@Bar@res@Foo@Bar@res@Foo@Bar@res@Foo@Bar@res@Foo@Bar@res@Foo@Bar@res@Foo@Bar@"
    )
    lock_resources = [
        cluster_getter.sanitize4path(r) if isinstance(r, str) else r for r in lock_resources
    ]
    use_resources = [
        cluster_getter.sanitize4path(r) if isinstance(r, str) else r for r in use_resources
    ]
    assert mark == "res-Foo_Bar_res_Foo_"
    assert lock_resources == ["_res", oneof, "res1"]
    assert use_resources == ["res2", oneof, "res_"]


def test_get_unsanitized():
    unsanitized = cluster_getter.get_unsanitized(["res1", "res2", "res$3", "res#%4"])
    assert unsanitized == ["res$3", "res#%4"]
