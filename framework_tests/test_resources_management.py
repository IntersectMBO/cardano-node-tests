from cardano_node_tests.cluster_management import resources_management

ALL_POOLS = frozenset({"pool1", "pool2", "pool3", "pool4", "pool5"})


def test_get_lockable():
    resources_to_lock = [resources_management.OneOf(ALL_POOLS)]
    resources_locked = ["pool1", "pool2"]
    resources_used = ["pool3", "pool4"]
    selected = resources_management.get_resources(
        resources=resources_to_lock,
        unavailable=[*resources_locked, *resources_used],
    )
    assert selected == ["pool5"]


def test_get_usable():
    resources_to_use = [resources_management.OneOf(ALL_POOLS)]
    resources_locked = ["pool1", "pool2"]
    selected = resources_management.get_resources(
        resources=resources_to_use, unavailable=resources_locked
    )
    usable_expected = ALL_POOLS - set(resources_locked)
    assert len(selected) == 1
    assert selected[0] in usable_expected


def test_get_oneof_multi():
    resources_to_use = [
        "pool2",
        resources_management.OneOf(ALL_POOLS),
        resources_management.OneOf(ALL_POOLS),
    ]
    resources_locked = ["pool1"]
    selected = resources_management.get_resources(
        resources=resources_to_use, unavailable=resources_locked
    )
    usable_expected = ALL_POOLS - set(resources_locked)

    assert len(selected) == 3
    assert "pool2" in selected

    oneof_selected = set(selected) - {"pool2"}
    assert set(oneof_selected).intersection(usable_expected)


def test_oneof_empty():
    resources_locked = ALL_POOLS
    resources_to_use = [resources_management.OneOf(ALL_POOLS)]
    selected = resources_management.get_resources(
        resources=resources_to_use, unavailable=resources_locked
    )
    assert len(selected) == 0
