"""Юнит-тесты PaymentService (логика переводов, межбанк замокан)."""
from decimal import Decimal

import pytest
from sqlalchemy import select

import models
from services.payment_service import PaymentService


async def _seed(session_maker, a_balance="1000", b_balance="0"):
    async with session_maker() as s:
        c1 = models.Client(person_id="p1", client_type="individual", full_name="p1")
        s.add(c1); await s.flush()
        s.add(models.Account(client_id=c1.id, account_number="AAA",
                             account_type="checking", balance=Decimal(a_balance), status="active"))
        c2 = models.Client(person_id="p2", client_type="individual", full_name="p2")
        s.add(c2); await s.flush()
        s.add(models.Account(client_id=c2.id, account_number="BBB",
                             account_type="checking", balance=Decimal(b_balance), status="active"))
        await s.commit()


async def test_negative_amount(session_maker):
    await _seed(session_maker)
    async with session_maker() as s:
        with pytest.raises(ValueError):
            await PaymentService.initiate_payment(s, "AAA", "BBB", Decimal("-1"))


async def test_same_account(session_maker):
    await _seed(session_maker)
    async with session_maker() as s:
        with pytest.raises(ValueError):
            await PaymentService.initiate_payment(s, "AAA", "AAA", Decimal("10"))


async def test_insufficient_funds(session_maker):
    await _seed(session_maker, a_balance="5")
    async with session_maker() as s:
        with pytest.raises(ValueError):
            await PaymentService.initiate_payment(s, "AAA", "BBB", Decimal("100"))


async def test_internal_transfer(session_maker):
    await _seed(session_maker)
    async with session_maker() as s:
        payment, interbank = await PaymentService.initiate_payment(s, "AAA", "BBB", Decimal("300"))
        assert payment.status == "AcceptedSettlementCompleted"
        assert interbank is None
        txs = (await s.execute(select(models.Transaction))).scalars().all()
        assert {t.direction for t in txs} == {"debit", "credit"}


async def test_interbank_success(session_maker, monkeypatch):
    await _seed(session_maker)

    async def fake_send(**kwargs):
        return True

    monkeypatch.setattr(PaymentService, "_send_interbank_transfer", fake_send)
    async with session_maker() as s:
        payment, interbank = await PaymentService.initiate_payment(
            s, "AAA", "CCC", Decimal("200"), target_bank_hint="abank")
        assert payment.status == "AcceptedSettlementCompleted"
        assert interbank is not None and interbank.status == "completed"
        cap = (await s.execute(
            select(models.BankCapital).where(models.BankCapital.bank_code == "vbank")
        )).scalar_one()
        assert cap.capital == Decimal("3500000.00") - Decimal("200")


async def test_interbank_failure_refunds(session_maker, monkeypatch):
    await _seed(session_maker)

    async def fake_send(**kwargs):
        return False

    monkeypatch.setattr(PaymentService, "_send_interbank_transfer", fake_send)
    async with session_maker() as s:
        payment, interbank = await PaymentService.initiate_payment(
            s, "AAA", "CCC", Decimal("200"), target_bank_hint="abank")
        assert payment.status == "Rejected"
        assert interbank is not None and interbank.status == "failed"
        acc = (await s.execute(
            select(models.Account).where(models.Account.account_number == "AAA")
        )).scalar_one()
        assert acc.balance == Decimal("1000")
