"""Unit tests for batched testnet cleanup without a running node."""

import dataclasses
import json
import pathlib as pl
import subprocess
import typing as tp
from unittest import mock

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import testnet_cleanup as cleanup


def _address(name: str) -> clusterlib.AddressRecord:
    """Create a payment or stake record without writing key files."""
    return clusterlib.AddressRecord(
        address=name, vkey_file=pl.Path(f"{name}.vkey"), skey_file=pl.Path(f"{name}.skey")
    )


def _info(
    address: str = "", *, rewards: int = 0, delegation: str = "", deposit: int = 2_000_000
) -> clusterlib.StakeAddrInfo:
    """Create queried stake-account information, including unregistered accounts."""
    return clusterlib.StakeAddrInfo(
        address=address,
        delegation="",
        reward_account_balance=rewards,
        registration_deposit=deposit,
        vote_delegation=delegation,
    )


def _action(name: str, *, deposit: int = 0) -> cleanup._CertificateAction:
    """Create one certificate action for submission tests."""
    return cleanup._CertificateAction(
        address=name,
        certificates=[pl.Path(f"{name}.cert")],
        signing_keys=[pl.Path(f"{name}.skey")],
        deposit=deposit,
    )


@pytest.fixture
def cluster(monkeypatch: pytest.MonkeyPatch) -> mock.MagicMock:
    """Provide a cluster mock with real scalar values and no discovery sleeps."""
    monkeypatch.setattr(cleanup.time, "sleep", lambda _: None)
    obj = mock.MagicMock()
    obj.g_query.get_address_deposit.return_value = 2_000_000
    obj.g_query.get_drep_deposit.return_value = 500_000_000
    obj.g_query.get_utxo.return_value = []
    obj.g_address.gen_payment_addr_and_keys.side_effect = _address
    obj.g_stake_address.gen_stake_addr_registration_cert.return_value = pl.Path("register.cert")
    obj.g_stake_address.gen_vote_delegation_cert.return_value = pl.Path("delegate.cert")
    obj.g_stake_address.gen_stake_addr_deregistration_cert.return_value = pl.Path("deregister.cert")
    obj.g_governance.drep.gen_retirement_cert.return_value = pl.Path("retire.cert")
    return obj


def test_certificate_batch_balancing(cluster: mock.MagicMock):
    """Aggregate certificates, deposits and withdrawals, deduplicating witnesses."""
    actions = [_action("stake0", deposit=-2_000_000), _action("stake1", deposit=-3_000_000)]
    actions[0].withdrawals = [clusterlib.TxOut(address="stake0", amount=123)]
    actions[1].signing_keys.append(actions[0].signing_keys[0])
    result = cleanup._submit_certificate_batches(
        cluster_obj=cluster, payment_addr=_address("payer"), actions=actions, name_template="test"
    )
    assert result == actions
    cluster.g_transaction.send_tx.assert_called_once()
    kwargs = cluster.g_transaction.send_tx.call_args.kwargs
    assert kwargs["deposit"] == -5_000_000
    assert kwargs["withdrawals"] == actions[0].withdrawals
    assert kwargs["tx_files"].certificate_files == [pl.Path("stake0.cert"), pl.Path("stake1.cert")]
    assert kwargs["tx_files"].signing_key_files == [
        pl.Path("payer.skey"),
        pl.Path("stake0.skey"),
        pl.Path("stake1.skey"),
    ]
    assert kwargs.get("verify_tx", True)


def test_certificate_batch_limit(cluster: mock.MagicMock):
    """Bound certificate batches and use distinct transaction artifact names."""
    actions = [_action(f"stake{i}") for i in range(cleanup.CERT_BATCH_SIZE + 1)]
    assert (
        cleanup._submit_certificate_batches(
            cluster_obj=cluster,
            payment_addr=_address("payer"),
            actions=actions,
            name_template="test",
        )
        == actions
    )
    calls = cluster.g_transaction.send_tx.call_args_list
    assert [len(c.kwargs["tx_files"].certificate_files) for c in calls] == [20, 1]
    assert len({c.kwargs["tx_name"] for c in calls}) == len(calls)


def test_rejected_certificate_isolation(cluster: mock.MagicMock):
    """A stale credential does not prevent the other certificates being submitted."""
    actions = [_action("good"), _action("stale")]

    def send(**kwargs: object) -> None:
        files = kwargs["tx_files"]
        assert isinstance(files, clusterlib.TxFiles)
        if pl.Path("stale.cert") in files.certificate_files:
            msg = "ConwayDRepNotRegistered"
            raise clusterlib.CLIError(msg)

    cluster.g_transaction.send_tx.side_effect = send
    assert (
        cleanup._submit_certificate_batches(
            cluster_obj=cluster,
            payment_addr=_address("payer"),
            actions=actions,
            name_template="test",
        )
        == actions[:1]
    )
    assert cluster.g_transaction.send_tx.call_count == 3


def test_oversized_certificate_batch(cluster: mock.MagicMock):
    """Split an oversized transaction into smaller confirmed batches."""
    actions = [_action("a"), _action("b")]
    cluster.g_transaction.send_tx.side_effect = [clusterlib.CLIError("MaxTxSizeUTxO"), None, None]
    assert (
        cleanup._submit_certificate_batches(
            cluster_obj=cluster,
            payment_addr=_address("payer"),
            actions=actions,
            name_template="test",
        )
        == actions
    )


@pytest.mark.parametrize("error", ["didn't make it to the chain", "Failed to resubmit"])
def test_uncertain_submission_not_split(cluster: mock.MagicMock, error: str):
    """Do not retry parts of a transaction whose confirmation is uncertain."""
    cluster.g_transaction.send_tx.side_effect = clusterlib.CLIError(error)
    assert not cleanup._submit_certificate_batches(
        cluster_obj=cluster,
        payment_addr=_address("payer"),
        actions=[_action("a"), _action("b")],
        name_template="test",
    )
    cluster.g_transaction.send_tx.assert_called_once()


def test_unregistered_accounts_recover_rewards(cluster: mock.MagicMock):
    """Preserve re-registration and query newly visible rewards before deregistration."""
    cluster.g_query.get_stake_addr_info.side_effect = [
        _info(),
        _info(),
        _info("stake0", rewards=123, delegation="alwaysAbstain"),
        _info("stake1", rewards=456, delegation="alwaysAbstain"),
    ]
    cleanup._cleanup_stake_addresses(
        cluster_obj=cluster,
        stake_addrs=[_address("stake0"), _address("stake1")],
        payment_addr=_address("payer"),
        deposit_amt=2_000_000,
        name_template="test",
    )
    calls = cluster.g_transaction.send_tx.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["deposit"] == 4_000_000
    assert len(calls[0].kwargs["tx_files"].certificate_files) == 4
    assert calls[1].kwargs["deposit"] == -4_000_000
    assert calls[1].kwargs["withdrawals"] == [
        clusterlib.TxOut(address="stake0", amount=123),
        clusterlib.TxOut(address="stake1", amount=456),
    ]
    cluster.wait_for_new_block.assert_not_called()


def test_registered_account_delegation(cluster: mock.MagicMock):
    """Delegate votes only for registered accounts with rewards and no delegation."""
    cluster.g_query.get_stake_addr_info.side_effect = [
        _info("stake0", rewards=123),
        _info("stake0", rewards=123, delegation="alwaysAbstain", deposit=1_000_000),
    ]
    cleanup._cleanup_stake_addresses(
        cluster_obj=cluster,
        stake_addrs=[_address("stake0")],
        payment_addr=_address("payer"),
        deposit_amt=2_000_000,
        name_template="test",
    )
    cluster.g_stake_address.gen_stake_addr_registration_cert.assert_not_called()
    cluster.g_stake_address.gen_vote_delegation_cert.assert_called_once()
    calls = cluster.g_transaction.send_tx.call_args_list
    assert [c.kwargs["deposit"] for c in calls] == [0, -1_000_000]


def test_registered_zero_reward_account(cluster: mock.MagicMock):
    """Deregister a registered account directly when no vote delegation is needed."""
    cluster.g_query.get_stake_addr_info.return_value = _info("stake0")
    cleanup._cleanup_stake_addresses(
        cluster_obj=cluster,
        stake_addrs=[_address("stake0")],
        payment_addr=_address("payer"),
        deposit_amt=2_000_000,
        name_template="test",
    )
    cluster.g_stake_address.gen_vote_delegation_cert.assert_not_called()
    cluster.g_transaction.send_tx.assert_called_once()
    assert cluster.g_transaction.send_tx.call_args.kwargs["withdrawals"] == []


def test_failed_registration_not_deregistered(cluster: mock.MagicMock):
    """Do not build deregistration certificates after rejected re-registration."""
    cluster.g_query.get_stake_addr_info.return_value = _info()
    cluster.g_transaction.send_tx.side_effect = clusterlib.CLIError("rejected")
    cleanup._cleanup_stake_addresses(
        cluster_obj=cluster,
        stake_addrs=[_address("stake0")],
        payment_addr=_address("payer"),
        deposit_amt=2_000_000,
        name_template="test",
    )
    cluster.g_stake_address.gen_stake_addr_deregistration_cert.assert_not_called()
    cluster.g_transaction.send_tx.assert_called_once()


def test_registration_visibility_retries_together(cluster: mock.MagicMock):
    """Wait for lagging registration queries once per round, not once per account."""
    cluster.g_query.get_stake_addr_info.side_effect = [
        _info(),
        _info(),
        _info(),
        _info(),
        _info("stake0", delegation="alwaysAbstain"),
        _info("stake1", delegation="alwaysAbstain"),
    ]
    cleanup._cleanup_stake_addresses(
        cluster_obj=cluster,
        stake_addrs=[_address("stake0"), _address("stake1")],
        payment_addr=_address("payer"),
        deposit_amt=2_000_000,
        name_template="test",
    )
    cluster.wait_for_new_block.assert_called_once()
    assert cluster.g_transaction.send_tx.call_count == 2


def test_unqueryable_registration_not_deregistered(cluster: mock.MagicMock):
    """Skip an account that remains unqueryable after confirmed registration."""
    cluster.g_query.get_stake_addr_info.return_value = _info()
    cleanup._cleanup_stake_addresses(
        cluster_obj=cluster,
        stake_addrs=[_address("stake0")],
        payment_addr=_address("payer"),
        deposit_amt=2_000_000,
        name_template="test",
    )
    cluster.g_stake_address.gen_stake_addr_deregistration_cert.assert_not_called()
    assert cluster.wait_for_new_block.call_count == 2


def test_stake_batches_recycle_deposits(cluster: mock.MagicMock):
    """Complete deregistration before re-registering the next funded batch."""
    addresses = [_address(f"stake{i}") for i in range(cleanup.CERT_BATCH_SIZE + 1)]
    queried = set()

    def info(address: str) -> clusterlib.StakeAddrInfo:
        if address in queried:
            return _info(address, delegation="alwaysAbstain")
        queried.add(address)
        return _info()

    cluster.g_query.get_stake_addr_info.side_effect = info
    cleanup._cleanup_stake_addresses(
        cluster_obj=cluster,
        stake_addrs=addresses,
        payment_addr=_address("payer"),
        deposit_amt=2_000_000,
        name_template="test",
    )
    calls = cluster.g_transaction.send_tx.call_args_list
    assert [c.kwargs["deposit"] for c in calls] == [40_000_000, -40_000_000, 2_000_000, -2_000_000]
    assert len({c.kwargs["tx_name"] for c in calls}) == len(calls)


def test_utxo_queries_batched(cluster: mock.MagicMock):
    """Query bounded address groups and associate each input with its signing key."""
    addresses = [_address(f"payment{i}") for i in range(cleanup.QUERY_BATCH_SIZE + 1)]
    first = clusterlib.UTXOData(utxo_hash="tx", utxo_ix=0, amount=10, address=addresses[0].address)
    last = dataclasses.replace(first, utxo_ix=1, address=addresses[-1].address)
    cluster.g_query.get_utxo.side_effect = [[first], [last]]
    assert cleanup.get_tx_inputs(cluster_obj=cluster, src_addrs=addresses) == [
        ([first], addresses[0].skey_file),
        ([last], addresses[-1].skey_file),
    ]
    assert cluster.g_query.get_utxo.call_args_list == [
        mock.call(address=[a.address for a in addresses[:-1]]),
        mock.call(address=[addresses[-1].address]),
    ]


def test_utxo_tokens_excluded_datums_preserved(cluster: mock.MagicMock):
    """Exclude entire multi-asset inputs, preserving spendable key inputs with datum."""
    address = _address("payment")
    ada = clusterlib.UTXOData(utxo_hash="tx", utxo_ix=0, amount=10, address=address.address)
    token = dataclasses.replace(ada, coin="policy.asset", amount=1)
    datum = dataclasses.replace(ada, utxo_ix=1, datum_hash="datum")
    cluster.g_query.get_utxo.return_value = [ada, token, datum]
    assert cleanup.get_tx_inputs(cluster_obj=cluster, src_addrs=[address]) == [
        ([datum], address.skey_file)
    ]


def test_empty_cleanup_no_funding(cluster: mock.MagicMock, tmp_path: pl.Path):
    """Empty artifact directories cause no funding, certificate or return transactions."""
    cleanup.cleanup_addresses(cluster_obj=cluster, location=tmp_path, faucet_payment=_address("f"))
    cleanup.cleanup_certs(cluster_obj=cluster, location=tmp_path, faucet_payment=_address("f"))
    cluster.g_address.gen_payment_addr_and_keys.assert_not_called()
    cluster.g_transaction.send_tx.assert_not_called()
    cluster.g_query.get_utxo.assert_not_called()
    cluster.wait_for_new_block.assert_not_called()


def test_payment_only_cleanup_no_funding(cluster: mock.MagicMock, tmp_path: pl.Path):
    """Payment-only discovery needs no fee-paying helper addresses."""
    for suffix, content in [("addr", "payment"), ("vkey", "key"), ("skey", "key")]:
        (tmp_path / f"payment.{suffix}").write_text(content)
    cleanup.cleanup_addresses(cluster_obj=cluster, location=tmp_path, faucet_payment=_address("f"))
    cluster.g_address.gen_payment_addr_and_keys.assert_not_called()
    cluster.g_query.get_utxo.assert_called_once_with(address=["payment"])
    cluster.g_transaction.send_tx.assert_not_called()


@pytest.mark.parametrize("with_stake", [False, True])
def test_actual_faucet_excluded_from_discovery(
    cluster: mock.MagicMock,
    tmp_path: pl.Path,
    monkeypatch: pytest.MonkeyPatch,
    with_stake: bool,
):
    """Exclude a renamed faucet before caching inputs that worker funding spends."""
    artifacts = [("funding-wallet", "faucet_address"), ("payment", "payment")]
    if with_stake:
        artifacts.append(("account_stake", "stake"))
    for name, address in artifacts:
        for suffix, content in [("addr", address), ("vkey", "key"), ("skey", "key")]:
            (tmp_path / f"{name}.{suffix}").write_text(content)

    payment_input = clusterlib.UTXOData(
        utxo_hash="payment_tx", utxo_ix=0, amount=10_000_000, address="payment"
    )
    worker_input = dataclasses.replace(
        payment_input, utxo_hash="funding_tx", address="addrs_cleanup0"
    )
    faucet_input = dataclasses.replace(
        payment_input, utxo_hash="faucet_tx", address="faucet_address"
    )
    available = {u.address: [u] for u in [payment_input, worker_input, faucet_input]}
    cluster.g_query.get_utxo.side_effect = lambda address: [
        u for a in address for u in available[a]
    ]
    monkeypatch.setattr(cleanup, "_cleanup_stake_addresses", mock.Mock())
    return_funds = mock.Mock()
    monkeypatch.setattr(cleanup, "return_funds_to_faucet", return_funds)

    cleanup.cleanup_addresses(
        cluster_obj=cluster, location=tmp_path, faucet_payment=_address("faucet_address")
    )

    expected_inputs = [([payment_input], tmp_path / "payment.skey")]
    if with_stake:
        expected_inputs.append(([worker_input], pl.Path("addrs_cleanup0.skey")))
        cluster.g_transaction.send_tx.assert_called_once()
    else:
        cluster.g_transaction.send_tx.assert_not_called()
    return_funds.assert_called_once_with(
        cluster_obj=cluster, tx_inputs=expected_inputs, faucet_address="faucet_address"
    )
    assert all(
        "faucet_address" not in c.kwargs["address"] for c in cluster.g_query.get_utxo.call_args_list
    )


def _write_drep(directory: pl.Path, name: str, identity: str) -> pl.Path:
    """Write the artifacts used to locate and deduplicate DRep keys."""
    (directory / f"{name}_drep_reg.cert").write_text("certificate")
    vkey = directory / f"{name}_drep.vkey"
    vkey.write_text(json.dumps({"cborHex": identity}))
    vkey.with_suffix(".skey").write_text("key")
    return vkey


def test_drep_cleanup_filters_and_batches(cluster: mock.MagicMock, tmp_path: pl.Path):
    """Deduplicate keys, skip retired DReps and batch actual deposit refunds."""
    _write_drep(tmp_path, "active0", "00")
    _write_drep(tmp_path, "duplicate", "00")
    _write_drep(tmp_path, "active1", "01")
    _write_drep(tmp_path, "retired", "02")
    (tmp_path / "missing_drep_reg.cert").write_text("certificate")
    cluster.g_query.get_drep_state.side_effect = lambda drep_vkey_file: (
        [[{}, {"deposit": 123}]]
        if json.loads(drep_vkey_file.read_text())["cborHex"] in {"00", "01"}
        else []
    )
    cleanup.cleanup_certs(cluster_obj=cluster, location=tmp_path, faucet_payment=_address("f"))
    assert cluster.g_query.get_drep_state.call_count == 3
    cluster.g_address.gen_payment_addr_and_keys.assert_called_once_with(name="certs_cleanup0")
    calls = cluster.g_transaction.send_tx.call_args_list
    assert len(calls) == 2
    assert len(calls[0].kwargs["txouts"]) == 1
    assert calls[1].kwargs["deposit"] == -246
    assert len(calls[1].kwargs["tx_files"].certificate_files) == 2


def test_retired_dreps_no_funding(cluster: mock.MagicMock, tmp_path: pl.Path):
    """Do not fund helpers when all discovered DReps are already retired."""
    _write_drep(tmp_path, "retired", "00")
    cluster.g_query.get_drep_state.return_value = []
    cleanup.cleanup_certs(cluster_obj=cluster, location=tmp_path, faucet_payment=_address("f"))
    cluster.g_address.gen_payment_addr_and_keys.assert_not_called()
    cluster.g_transaction.send_tx.assert_not_called()


@pytest.mark.parametrize("content", ["not json", "{}"])
def test_unreadable_drep_keys_skipped(cluster: mock.MagicMock, tmp_path: pl.Path, content: str):
    """Malformed key artifacts do not prevent cleanup of other credentials."""
    vkey = _write_drep(tmp_path, "invalid", "00")
    vkey.write_text(content)
    cleanup.cleanup_certs(cluster_obj=cluster, location=tmp_path, faucet_payment=_address("f"))
    cluster.g_query.get_drep_state.assert_not_called()
    cluster.g_transaction.send_tx.assert_not_called()


def test_invalid_drep_query_skipped(cluster: mock.MagicMock, tmp_path: pl.Path):
    """A key rejected by the CLI does not require funding a retirement worker."""
    _write_drep(tmp_path, "invalid", "00")
    cluster.g_query.get_drep_state.side_effect = clusterlib.CLIError("invalid verification key")
    cleanup.cleanup_certs(cluster_obj=cluster, location=tmp_path, faucet_payment=_address("f"))
    cluster.g_transaction.send_tx.assert_not_called()


def test_drep_worker_exceptions_propagate(cluster: mock.MagicMock, tmp_path: pl.Path):
    """Surface unexpected worker failures instead of silently ignoring futures."""
    _write_drep(tmp_path, "active", "00")
    cluster.g_query.get_drep_state.side_effect = RuntimeError("query failed")
    with pytest.raises(RuntimeError, match="query failed"):
        cleanup.cleanup_certs(cluster_obj=cluster, location=tmp_path, faucet_payment=_address("f"))


def test_return_funds_shared_verification(cluster: mock.MagicMock, monkeypatch: pytest.MonkeyPatch):
    """Verify disjoint return transactions together instead of waiting fixed blocks."""
    inputs = [
        clusterlib.UTXOData(utxo_hash="tx", utxo_ix=i, amount=10_000_000, address="payment")
        for i in range(101)
    ]
    cluster.g_transaction.send_tx.side_effect = [
        mock.Mock(txins=inputs[:100]),
        mock.Mock(txins=inputs[100:]),
    ]
    check = mock.Mock()
    monkeypatch.setattr(cleanup.clusterlib_utils, "check_txins_spent", check)
    cleanup.return_funds_to_faucet(
        cluster_obj=cluster, tx_inputs=[(inputs, pl.Path("payment.skey"))], faucet_address="f"
    )
    check.assert_called_once_with(cluster_obj=cluster, txins=[inputs[0], inputs[100]])
    assert [len(c.kwargs["txins"]) for c in cluster.g_transaction.send_tx.call_args_list] == [
        100,
        1,
    ]
    assert all(not c.kwargs["verify_tx"] for c in cluster.g_transaction.send_tx.call_args_list)
    cluster.wait_for_new_block.assert_not_called()


def test_return_funds_fee_retry(cluster: mock.MagicMock, monkeypatch: pytest.MonkeyPatch):
    """Preserve fee retries and verify only successfully submitted transactions."""
    utxo = clusterlib.UTXOData(utxo_hash="tx", utxo_ix=0, amount=10_000_000, address="payment")
    cluster.g_transaction.send_tx.side_effect = [
        clusterlib.CLIError("FeeTooSmallUTxO"),
        mock.Mock(txins=[utxo]),
    ]
    check = mock.Mock()
    monkeypatch.setattr(cleanup.clusterlib_utils, "check_txins_spent", check)
    cleanup.return_funds_to_faucet(
        cluster_obj=cluster, tx_inputs=[([utxo], pl.Path("payment.skey"))], faucet_address="f"
    )
    assert [
        c.kwargs["witness_count_add"] for c in cluster.g_transaction.send_tx.call_args_list
    ] == [2, 7]
    check.assert_called_once_with(cluster_obj=cluster, txins=[utxo])
    calls = cluster.g_transaction.send_tx.call_args_list
    assert len({c.kwargs["tx_name"] for c in calls}) == len(calls)


def test_failed_returns_no_wait(cluster: mock.MagicMock, monkeypatch: pytest.MonkeyPatch):
    """Do not wait for transactions that were rejected before submission."""
    utxo = clusterlib.UTXOData(utxo_hash="tx", utxo_ix=0, amount=10_000_000, address="payment")
    cluster.g_transaction.send_tx.side_effect = clusterlib.CLIError("rejected")
    check = mock.Mock()
    monkeypatch.setattr(cleanup.clusterlib_utils, "check_txins_spent", check)
    cleanup.return_funds_to_faucet(
        cluster_obj=cluster, tx_inputs=[([utxo], pl.Path("payment.skey"))], faucet_address="f"
    )
    check.assert_not_called()
    cluster.wait_for_new_block.assert_not_called()


def test_unconfirmed_returns_logged(
    cluster: mock.MagicMock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """Preserve best-effort cleanup when collective return verification fails."""
    utxo = clusterlib.UTXOData(utxo_hash="tx", utxo_ix=0, amount=10_000_000, address="payment")
    cluster.g_transaction.send_tx.return_value = mock.Mock(txins=[utxo])
    monkeypatch.setattr(
        cleanup.clusterlib_utils,
        "check_txins_spent",
        mock.Mock(side_effect=AssertionError("unspent")),
    )
    cleanup.return_funds_to_faucet(
        cluster_obj=cluster, tx_inputs=[([utxo], pl.Path("payment.skey"))], faucet_address="f"
    )
    assert "Some return-funds transactions were not confirmed" in caplog.text


def _payment_inputs(names: list[str], *, per_address: int = 2) -> cleanup.TxInputGroup:
    """Create multiple original UTxOs per payment address."""
    return [
        (
            [
                clusterlib.UTXOData(
                    utxo_hash=f"tx_{name}", utxo_ix=i, amount=10_000_000, address=name
                )
                for i in range(per_address)
            ],
            pl.Path(f"{name}.skey"),
        )
        for name in names
    ]


def _payment_sender(
    bad: set[str], *, reason: str = "MissingVKeyWitnessesUTXOW"
) -> tp.Callable[..., mock.Mock]:
    """Reject any transaction containing a specified address, without submitting it."""

    def send(*, txins: list[clusterlib.UTXOData], **_kwargs: tp.Any) -> mock.Mock:
        if any(u.address in bad for u in txins):
            msg = f"ShelleyTxValidationError ({reason} bad_key)"
            raise clusterlib.CLIError(msg)
        return mock.Mock(txins=txins)

    return send


@pytest.fixture
def payment_check(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    """Record collective confirmation without making node queries or waiting."""
    check = mock.Mock()
    monkeypatch.setattr(cleanup.clusterlib_utils, "check_txins_spent", check)
    return check


def test_payment_rejection_recovers_unaffected(
    cluster: mock.MagicMock, payment_check: mock.Mock, caplog: pytest.LogCaptureFixture
):
    """Recursively isolate one bad address without separating its original UTxOs."""
    groups = _payment_inputs(["a", "bad", "c", "d"])
    original, _ = cleanup.flatten_tx_inputs(groups)
    cluster.g_query.get_utxo.return_value = original
    cluster.g_transaction.send_tx.side_effect = _payment_sender({"bad"})
    cleanup.return_funds_to_faucet(
        cluster_obj=cluster, tx_inputs=groups, faucet_address="f", tx_name="isolate"
    )
    calls = cluster.g_transaction.send_tx.call_args_list
    assert len(calls) == 5
    successful = [
        c.kwargs["txins"] for c in calls if all(u.address != "bad" for u in c.kwargs["txins"])
    ]
    assert {u.address for txins in successful for u in txins} == {"a", "c", "d"}
    for call in calls:
        txins = call.kwargs["txins"]
        for address in {u.address for u in txins}:
            assert [u for u in txins if u.address == address] == [
                u for u in original if u.address == address
            ]
    assert len({c.kwargs["tx_name"] for c in calls}) == len(calls)
    payment_check.assert_called_once_with(
        cluster_obj=cluster, txins=[txins[0] for txins in successful]
    )
    assert "addresses=['bad']" in caplog.text
    assert "tx_bad#0" in caplog.text
    assert "tx_bad#1" in caplog.text
    assert "MissingVKeyWitnessesUTXOW" in caplog.text
    cluster.wait_for_new_block.assert_not_called()


def test_missing_script_witness_recovers_normal_address(
    cluster: mock.MagicMock, payment_check: mock.Mock, caplog: pytest.LogCaptureFixture
):
    """Isolate script-locked inputs without abandoning a normal address's funds."""
    groups = _payment_inputs(["normal", "script_locked"])
    original, _ = cleanup.flatten_tx_inputs(groups)
    cluster.g_query.get_utxo.return_value = original
    cluster.g_transaction.send_tx.side_effect = _payment_sender(
        {"script_locked"}, reason="MissingScriptWitnessesUTXOW"
    )
    cleanup.return_funds_to_faucet(cluster_obj=cluster, tx_inputs=groups, faucet_address="f")
    calls = cluster.g_transaction.send_tx.call_args_list
    assert [c.kwargs["txins"] for c in calls] == [original, groups[0][0], groups[1][0]]
    payment_check.assert_called_once_with(cluster_obj=cluster, txins=[groups[0][0][0]])
    assert "addresses=['script_locked']" in caplog.text
    assert "MissingScriptWitnessesUTXOW" in caplog.text
    cluster.wait_for_new_block.assert_not_called()


def test_payment_rejection_continues_subsequent_batches(
    cluster: mock.MagicMock, payment_check: mock.Mock
):
    """A failing single-address batch does not prevent the next batch succeeding."""
    groups = _payment_inputs(["bad", "good"], per_address=100)
    cluster.g_transaction.send_tx.side_effect = _payment_sender({"bad"})
    cleanup.return_funds_to_faucet(cluster_obj=cluster, tx_inputs=groups, faucet_address="f")
    assert cluster.g_transaction.send_tx.call_count == 2
    cluster.g_query.get_utxo.assert_not_called()
    payment_check.assert_called_once_with(cluster_obj=cluster, txins=[groups[1][0][0]])


def test_payment_refresh_preserves_original_set(cluster: mock.MagicMock, payment_check: mock.Mock):
    """Remove stale inputs without sweeping new outputs returned by a refresh query."""
    groups = _payment_inputs(["stale", "good", "bad"])
    original, _ = cleanup.flatten_tx_inputs(groups)
    extra = dataclasses.replace(groups[1][0][0], utxo_hash="new_output")
    still_unspent = [groups[1][0][0], *groups[2][0], extra]
    cluster.g_query.get_utxo.return_value = still_unspent
    cluster.g_transaction.send_tx.side_effect = _payment_sender({"bad"})
    cleanup.return_funds_to_faucet(cluster_obj=cluster, tx_inputs=groups, faucet_address="f")
    for call in cluster.g_query.get_utxo.call_args_list:
        assert "address" not in call.kwargs
        assert set(call.kwargs["txin"]) <= {f"{u.utxo_hash}#{u.utxo_ix}" for u in original}
    successful = [
        c.kwargs["txins"]
        for c in cluster.g_transaction.send_tx.call_args_list
        if all(u.address != "bad" for u in c.kwargs["txins"])
    ]
    assert successful == [[groups[1][0][0]]]
    assert all(extra not in c.kwargs["txins"] for c in cluster.g_transaction.send_tx.call_args_list)
    payment_check.assert_called_once_with(cluster_obj=cluster, txins=[groups[1][0][0]])


def test_payment_refresh_drops_empty_groups(cluster: mock.MagicMock, payment_check: mock.Mock):
    """Do not submit empty children after all original inputs disappear."""
    groups = _payment_inputs(["a", "b"])
    cluster.g_transaction.send_tx.side_effect = _payment_sender({"a"})
    cluster.g_query.get_utxo.return_value = []
    cleanup.return_funds_to_faucet(cluster_obj=cluster, tx_inputs=groups, faucet_address="f")
    cluster.g_transaction.send_tx.assert_called_once()
    payment_check.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired("transaction submit", 300),
        TimeoutError("submission timed out"),
        clusterlib.CLIError("MempoolTxTooSlow retry budget exhausted"),
        clusterlib.CLIError("BadInputsUTxO"),
        clusterlib.CLIError("AllInputsAreSpent"),
        clusterlib.CLIError("Transaction didn't make it to the chain"),
        clusterlib.CLIError("Failed to resubmit the transaction"),
        clusterlib.CLIError("MuxError resource vanished (Broken pipe)"),
        clusterlib.CLIError("Network.Socket.connect: resource exhausted"),
        clusterlib.CLIError("socket connection refused"),
        OSError("socket closed"),
        clusterlib.CLIError("ValueNotConservedUTxO"),
        clusterlib.CLIError("unknown submission failure"),
        clusterlib.CLIError("MempoolTxTooSlow; Last rejection: FeeTooSmallUTxO"),
    ],
)
def test_payment_uncertain_or_systemic_no_split(
    cluster: mock.MagicMock, payment_check: mock.Mock, error: Exception
):
    """Never recursively retry infrastructure, ambiguous or systemic failures."""
    cluster.g_transaction.send_tx.side_effect = error
    cleanup.return_funds_to_faucet(
        cluster_obj=cluster, tx_inputs=_payment_inputs(["a", "b", "c"]), faucet_address="f"
    )
    cluster.g_transaction.send_tx.assert_called_once()
    cluster.g_query.get_utxo.assert_not_called()
    payment_check.assert_not_called()


def test_payment_chained_timeout_no_retry(cluster: mock.MagicMock, payment_check: mock.Mock):
    """A wrapper mentioning a ledger failure cannot hide an uncertain timeout cause."""
    error = clusterlib.CLIError("FeeTooSmallUTxO")
    error.__cause__ = subprocess.TimeoutExpired("transaction submit", 300)
    cluster.g_transaction.send_tx.side_effect = error
    cleanup.return_funds_to_faucet(
        cluster_obj=cluster, tx_inputs=_payment_inputs(["a", "b"]), faucet_address="f"
    )
    cluster.g_transaction.send_tx.assert_called_once()
    payment_check.assert_not_called()


def test_payment_unknown_wrapper_not_definite_rejection(
    cluster: mock.MagicMock, payment_check: mock.Mock
):
    """Do not classify an unknown wrapper using an earlier rejection in its cause."""
    error = clusterlib.CLIError("unknown submission outcome")
    error.__cause__ = clusterlib.CLIError("FeeTooSmallUTxO")
    cluster.g_transaction.send_tx.side_effect = error
    cleanup.return_funds_to_faucet(
        cluster_obj=cluster, tx_inputs=_payment_inputs(["a", "b"]), faucet_address="f"
    )
    cluster.g_transaction.send_tx.assert_called_once()
    payment_check.assert_not_called()


def test_payment_fee_retries_bounded(cluster: mock.MagicMock, payment_check: mock.Mock):
    """Exhaust fee retries once, rather than multiplying systemic failures by address."""
    cluster.g_transaction.send_tx.side_effect = clusterlib.CLIError("FeeTooSmallUTxO")
    cleanup.return_funds_to_faucet(
        cluster_obj=cluster, tx_inputs=_payment_inputs(["a", "b", "c"]), faucet_address="f"
    )
    calls = cluster.g_transaction.send_tx.call_args_list
    assert [c.kwargs["witness_count_add"] for c in calls] == [2, 7, 12]
    assert len({c.kwargs["tx_name"] for c in calls}) == len(calls)
    cluster.g_query.get_utxo.assert_not_called()
    payment_check.assert_not_called()


def test_payment_fee_retries_and_splits_have_unique_names(
    cluster: mock.MagicMock, payment_check: mock.Mock
):
    """Preserve bounded fee adjustments independently for parent and child groups."""
    groups = _payment_inputs(["good", "bad"])
    original, _ = cleanup.flatten_tx_inputs(groups)
    cluster.g_query.get_utxo.return_value = original
    cluster.g_transaction.send_tx.side_effect = [
        clusterlib.CLIError("FeeTooSmallUTxO"),
        clusterlib.CLIError("MissingVKeyWitnessesUTXOW"),
        clusterlib.CLIError("FeeTooSmallUTxO"),
        mock.Mock(txins=groups[0][0]),
        clusterlib.CLIError("MissingVKeyWitnessesUTXOW"),
    ]
    cleanup.return_funds_to_faucet(cluster_obj=cluster, tx_inputs=groups, faucet_address="f")
    calls = cluster.g_transaction.send_tx.call_args_list
    assert [c.kwargs["witness_count_add"] for c in calls] == [2, 7, 2, 7, 2]
    assert len({c.kwargs["tx_name"] for c in calls}) == len(calls)
    payment_check.assert_called_once_with(cluster_obj=cluster, txins=[groups[0][0][0]])


def test_payment_no_reuse_after_uncertain_submission(
    cluster: mock.MagicMock, payment_check: mock.Mock
):
    """Quarantine original IDs even when another signing-key group repeats them."""
    groups = _payment_inputs(["a"], per_address=100)
    repeated = (groups[0][0], pl.Path("alias.skey"))
    good = _payment_inputs(["good"])
    cluster.g_transaction.send_tx.side_effect = [
        subprocess.TimeoutExpired("submit", 300),
        mock.Mock(txins=good[0][0]),
    ]
    cleanup.return_funds_to_faucet(
        cluster_obj=cluster, tx_inputs=[*groups, repeated, *good], faucet_address="f"
    )
    calls = cluster.g_transaction.send_tx.call_args_list
    assert len(calls) == 2
    assert calls[1].kwargs["txins"] == good[0][0]
    payment_check.assert_called_once_with(cluster_obj=cluster, txins=[good[0][0][0]])


def test_payment_refresh_does_not_reintroduce_submitted_inputs(
    cluster: mock.MagicMock, payment_check: mock.Mock
):
    """An overbroad refresh cannot bring inputs from an earlier submitted batch back."""
    first = _payment_inputs(["first"], per_address=100)
    later = _payment_inputs(["good", "bad"])
    all_inputs, _ = cleanup.flatten_tx_inputs([*first, *later])
    cluster.g_query.get_utxo.return_value = all_inputs
    cluster.g_transaction.send_tx.side_effect = _payment_sender({"bad"})
    cleanup.return_funds_to_faucet(
        cluster_obj=cluster, tx_inputs=[*first, *later], faucet_address="f"
    )
    calls = cluster.g_transaction.send_tx.call_args_list
    assert all(u.address != "first" for c in calls[1:] for u in c.kwargs["txins"])
    payment_check.assert_called_once_with(
        cluster_obj=cluster, txins=[first[0][0][0], later[0][0][0]]
    )


def test_payment_refresh_infrastructure_failure_stops_branch(
    cluster: mock.MagicMock, payment_check: mock.Mock
):
    """A failed refresh stops isolation rather than triggering recursive retry storms."""
    cluster.g_transaction.send_tx.side_effect = _payment_sender({"bad"})
    cluster.g_query.get_utxo.side_effect = clusterlib.CLIError("connection refused")
    cleanup.return_funds_to_faucet(
        cluster_obj=cluster, tx_inputs=_payment_inputs(["good", "bad"]), faucet_address="f"
    )
    cluster.g_transaction.send_tx.assert_called_once()
    cluster.g_query.get_utxo.assert_called_once()
    payment_check.assert_not_called()
