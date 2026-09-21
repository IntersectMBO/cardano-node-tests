"""Helpers for creating and funding addresses and pool users used by tests."""

import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils.versions import VERSIONS

def get_conway_address_deposit(cluster_obj: clusterlib.ClusterLib) -> int:
    """Get stake address deposit amount - is required in Conway+."""
    stake_deposit_amt = -1
    if VERSIONS.transaction_era >= VERSIONS.CONWAY_FIRST:
        stake_deposit_amt = cluster_obj.g_query.get_address_deposit()

    return stake_deposit_amt


def _get_funded_addresses(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    create_func: tp.Callable[[], list],
    fund_idx: list[int] | None = None,
    caching_key: str = "",
    amount: int | None = None,
    min_amount: int | None = None,
) -> list:
    """Create and fund addresses.

    If `amount` and no `min_amount` is provided, fund once and never re-fund.
    If `amount` is not provided, re-fund 3 * `min_amount` when balance drops below `min_amount`.
    If both `amount` and `min_amount` are provided, re-fund `amount` when balance
    drops below `min_amount`.
    """
    no_refund = amount is not None and min_amount is None
    # Set a default minimum amount if none is provided
    drop_amount = min_amount or 50_000_000

    if no_refund:
        assert amount  # For mypy
        # Use the exact specified amount
        fund_amount = amount
        drop_amount = amount
    elif amount is not None:
        # Amount given: use it
        fund_amount = amount
    else:
        # No amount given: fund triple the minimum
        fund_amount = drop_amount * 3

    if caching_key:
        fixture_cache: cluster_management.FixtureCache[list | None]
        with cluster_manager.cache_fixture(key=caching_key) as fixture_cache:
            if fixture_cache.value is None:
                addrs = create_func()
                fixture_cache.value = addrs
            else:
                addrs = fixture_cache.value
                # If amount is explicitly specified, skip re-funding
                if no_refund:
                    return addrs

    else:
        addrs = create_func()

    # Fund source addresses
    selected_addrs = addrs if fund_idx is None else [addrs[i] for i in fund_idx]
    # The `selected_addrs` can be both `AddressRecord`s or `PoolUser`s
    payment_addrs = ((sa.payment if hasattr(sa, "payment") else sa) for sa in selected_addrs)
    fund_addrs: list[clusterlib.AddressRecord] = [
        a for a in payment_addrs if cluster_obj.g_query.get_address_balance(a.address) < drop_amount
    ]
    if fund_addrs:
        clusterlib_utils.fund_from_faucet(
            *fund_addrs,
            cluster_obj=cluster_obj,
            all_faucets=cluster_manager.cache.addrs_data,
            amount=fund_amount,
            tx_name=f"{name_template}_addrs",
            force=True,
        )

    return addrs


def get_payment_addrs(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    num: int,
    fund_idx: list[int] | None = None,
    caching_key: str = "",
    amount: int | None = None,
    min_amount: int | None = None,
    key_gen_method: clusterlib_utils.KeyGenMethods = clusterlib_utils.KeyGenMethods.DIRECT,
) -> list[clusterlib.AddressRecord]:
    """Create new payment addresses."""
    if num < 1:
        err = f"Number of addresses must be at least 1, got: {num}"
        raise ValueError(err)

    def _create_addrs() -> list[clusterlib.AddressRecord]:
        addrs = clusterlib_utils.create_payment_addr_records(
            *[f"{name_template}_fund_addr_{i}" for i in range(1, num + 1)],
            cluster_obj=cluster_obj,
            key_gen_method=key_gen_method,
        )
        return addrs

    return _get_funded_addresses(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        create_func=_create_addrs,
        fund_idx=fund_idx,
        caching_key=caching_key,
        amount=amount,
        min_amount=min_amount,
    )


def get_payment_addr(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    caching_key: str = "",
    amount: int | None = None,
    min_amount: int | None = None,
    key_gen_method: clusterlib_utils.KeyGenMethods = clusterlib_utils.KeyGenMethods.DIRECT,
) -> clusterlib.AddressRecord:
    """Create a single new payment address."""
    return get_payment_addrs(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        num=1,
        caching_key=caching_key,
        amount=amount,
        min_amount=min_amount,
        key_gen_method=key_gen_method,
    )[0]


def get_pool_users(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    num: int,
    fund_idx: list[int] | None = None,
    caching_key: str = "",
    amount: int | None = None,
    min_amount: int | None = None,
    payment_key_gen_method: clusterlib_utils.KeyGenMethods = clusterlib_utils.KeyGenMethods.DIRECT,
) -> list[clusterlib.PoolUser]:
    """Create new pool users."""
    if num < 1:
        err = f"Number of pool users must be at least 1, got: {num}"
        raise ValueError(err)

    def _create_pool_users() -> list[clusterlib.PoolUser]:
        users = clusterlib_utils.create_pool_users(
            cluster_obj=cluster_obj,
            name_template=f"{name_template}_pool_user",
            no_of_addr=num,
            payment_key_gen_method=payment_key_gen_method,
        )
        return users

    return _get_funded_addresses(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        create_func=_create_pool_users,
        fund_idx=fund_idx,
        caching_key=caching_key,
        amount=amount,
        min_amount=min_amount,
    )


def get_pool_user(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    caching_key: str = "",
    amount: int | None = None,
    min_amount: int | None = None,
    payment_key_gen_method: clusterlib_utils.KeyGenMethods = clusterlib_utils.KeyGenMethods.DIRECT,
) -> clusterlib.PoolUser:
    """Create a single new pool user."""
    return get_pool_users(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        num=1,
        caching_key=caching_key,
        amount=amount,
        min_amount=min_amount,
        payment_key_gen_method=payment_key_gen_method,
    )[0]


def get_registered_pool_user(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    caching_key: str = "",
    amount: int | None = None,
    min_amount: int | None = None,
) -> clusterlib.PoolUser:
    """Create new registered pool users."""
    pool_user = get_pool_user(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_obj,
        caching_key=caching_key,
        amount=amount,
        min_amount=min_amount,
    )

    if not cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address):
        # Register the stake address
        clusterlib_utils.register_stake_address(
            cluster_obj=cluster_obj,
            pool_user=pool_user,
            name_template=f"{name_template}_pool_user",
            deposit_amt=cluster_obj.g_query.get_address_deposit(),
        )

    return pool_user
