"""Must-fire / must-not-fire tests for the 85/15 settlement invariants (release gate G12-b).

scripts/reconcile_market.py runs these invariants against live data, which only proves
they hold. These tests prove the invariant function can actually fail, so a green
reconciliation run means something.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from reconcile_market import policy_failures  # noqa: E402

COMPLIANT_SPLITS = {"workspace": 8500, "platform": 1500}


def test_compliant_settlement_raises_nothing():
    # 3777 is not divisible by 20, so the seller share is floor(3210.45) and the platform
    # keeps the remainder; both halves must still add up to the order total.
    assert policy_failures(3777, 3210, dict(COMPLIANT_SPLITS)) == []


def test_rounding_keeps_the_platform_whole():
    assert policy_failures(100, 85, dict(COMPLIANT_SPLITS)) == []
    assert policy_failures(99, 84, dict(COMPLIANT_SPLITS)) == []


def test_overpaid_seller_is_caught():
    violations = policy_failures(3777, 3211, dict(COMPLIANT_SPLITS))
    assert violations, "a payout one unit above the 85% share must be rejected"


def test_underpaid_seller_is_caught():
    assert policy_failures(3777, 3200, dict(COMPLIANT_SPLITS))


def test_split_basis_point_drift_is_caught():
    assert policy_failures(3777, 3210, {"workspace": 9000, "platform": 1000})
    assert policy_failures(3777, 3210, {"workspace": 8500, "platform": 1499})


def test_missing_beneficiary_is_caught():
    assert policy_failures(3777, 3210, {"workspace": 8500})
    assert policy_failures(3777, 3210, {})


def test_payout_exceeding_order_total_is_caught():
    assert policy_failures(100, 120, dict(COMPLIANT_SPLITS))


def test_platform_squeezed_below_policy_is_caught():
    # Seller amount matches the floor, but the order total was shrunk so the platform
    # is left with less than its 15% of what was actually charged.
    assert policy_failures(3700, 3210, dict(COMPLIANT_SPLITS))
