"""Unit tests for batched testnet cleanup without a running node."""

import dataclasses
import json
import pathlib as pl
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
