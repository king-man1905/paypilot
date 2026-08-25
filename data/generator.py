"""Deterministic Synthetic Merchant Payment & Commerce Dataset Generator.

Generates realistic commerce transaction logs with intentional, reproducible
business leakages (UPI failure surge, mobile checkout drop-off, high-ticket failure,
fashion refund spikes) for PayPilot.
"""

from datetime import datetime, timedelta
from pathlib import Path
import argparse
import numpy as np
import pandas as pd


def generate_synthetic_transactions(
    num_records: int = 15000,
    seed: int = 42,
    start_date: datetime = None,
    output_path: Path = None,
) -> pd.DataFrame:
    """Generates a deterministic synthetic merchant dataset with injected revenue leakage patterns.

    Args:
        num_records: Total number of transactions to generate.
        seed: Random seed for 100% reproducibility.
        start_date: Starting date for the 90-day time window (defaults to 90 days before today).
        output_path: Optional path to save CSV output.

    Returns:
        pd.DataFrame: Deterministic merchant payment transactions.
    """
    rng = np.random.default_rng(seed)

    if start_date is None:
        start_date = datetime(2025, 11, 20, 0, 0, 0)

    # Core Dimensions
    merchant_id = "mer_paypilot_01"
    num_customers = max(1000, int(num_records * 0.25))
    customer_pool = [f"CUST_{i:05d}" for i in range(1, num_customers + 1)]
    
    # VIP customers pool (top 5%)
    vip_pool = set(customer_pool[: int(num_customers * 0.05)])

    categories = [
        "Electronics",
        "Fashion",
        "Grocery",
        "Beauty_Personal_Care",
        "Home_Kitchen",
    ]
    category_weights = [0.22, 0.28, 0.20, 0.16, 0.14]

    category_price_ranges = {
        "Electronics": (2500, 18000),
        "Fashion": (800, 4500),
        "Grocery": (300, 2200),
        "Beauty_Personal_Care": (400, 3200),
        "Home_Kitchen": (1200, 7500),
    }

    devices = ["Mobile_Android", "Mobile_iOS", "Desktop", "Tablet"]
    device_weights = [0.55, 0.22, 0.18, 0.05]

    payment_methods = ["UPI", "Credit_Card", "Debit_Card", "Netbanking", "Wallet"]
    method_weights = [0.52, 0.24, 0.12, 0.08, 0.04]

    # Pre-generate timestamps across 90 days with day-of-week and hourly curves
    total_seconds = 90 * 24 * 3600
    random_offsets = rng.uniform(0, total_seconds, size=num_records)
    random_offsets.sort()  # chronological order

    # Track customer history for realistic New vs Returning classification
    seen_customers = set()

    records = []

    for i in range(num_records):
        txn_id = f"TXN_{100000 + i + 1}"
        txn_time = start_date + timedelta(seconds=float(random_offsets[i]))
        day_index = (txn_time - start_date).days  # 0 to 89

        # Customer selection
        cust_id = customer_pool[rng.integers(0, len(customer_pool))]
        if cust_id in vip_pool:
            cust_type = "VIP"
        elif cust_id in seen_customers:
            cust_type = "RETURNING"
        else:
            cust_type = "NEW"
            seen_customers.add(cust_id)

        # Product category & Amount
        cat = rng.choice(categories, p=category_weights)
        min_p, max_p = category_price_ranges[cat]
        # Log-normal distribution for realistic cart values
        amount = round(float(rng.uniform(min_p, max_p)), 2)

        # Device & Payment Method
        device = rng.choice(devices, p=device_weights)
        method = rng.choice(payment_methods, p=method_weights)

        # --- Injected Business Leakage Logic ---
        # Baseline failure probabilities
        base_failure_prob = 0.08  # Healthy 92% baseline success rate

        # Injected Issue 1: Month 3 (Days 60-89) UPI Failure Spike on Mobile
        if day_index >= 60 and method == "UPI" and "Mobile" in device:
            # Failure rate rises to ~25.5% in Month 3
            failure_prob = 0.255
        # Injected Issue 2: High-Value Electronics Failure on Cards (> ₹8000)
        elif cat == "Electronics" and amount > 8000 and method in ["Credit_Card", "Debit_Card"]:
            failure_prob = 0.220
        # Injected Issue 3: Netbanking redirect drop on Mobile
        elif method == "Netbanking" and "Mobile" in device:
            failure_prob = 0.190
        # Month-wise gradual degradation (Month 1: 8%, Month 2: 12%, Month 3: 20%)
        elif day_index >= 60:
            failure_prob = 0.180
        elif day_index >= 30:
            failure_prob = 0.120
        else:
            failure_prob = base_failure_prob

        # VIP customers slightly lower failure (better limits/funds)
        if cust_type == "VIP":
            failure_prob = max(0.04, failure_prob * 0.7)

        # Determine transaction outcome
        rand_val = rng.random()
        
        # 4% of total attempts are abandoned/dropped before gateway completion
        drop_prob = 0.045 if "Mobile" in device else 0.025
        
        if rand_val < drop_prob:
            payment_status = "DROPPED"
            checkout_step = "PAYMENT_INITIATED"
            failure_reason = "USER_ABORTED"
            refund_status = "NO_REFUND"
        elif rand_val < (drop_prob + failure_prob):
            payment_status = "FAILED"
            checkout_step = "PAYMENT_INITIATED"
            
            # Specific Failure Reason Injection
            if method == "UPI" and "Mobile" in device and day_index >= 60:
                failure_reason = rng.choice(
                    ["UPI_APP_NOT_RESPONDING", "BANK_SERVER_TIMEOUT", "INVALID_OTP", "NETWORK_LATENCY"],
                    p=[0.45, 0.35, 0.10, 0.10],
                )
            elif cat == "Electronics" and amount > 8000:
                failure_reason = rng.choice(
                    ["RISK_DECLINE", "BANK_SERVER_TIMEOUT", "INSUFFICIENT_FUNDS", "INVALID_OTP"],
                    p=[0.40, 0.30, 0.20, 0.10],
                )
            elif method == "Netbanking":
                failure_reason = rng.choice(
                    ["BANK_SERVER_TIMEOUT", "GATEWAY_DOWNTIME", "USER_ABORTED", "NETWORK_LATENCY"],
                    p=[0.40, 0.30, 0.20, 0.10],
                )
            else:
                failure_reason = rng.choice(
                    [
                        "BANK_SERVER_TIMEOUT",
                        "INSUFFICIENT_FUNDS",
                        "USER_ABORTED",
                        "INVALID_OTP",
                        "NETWORK_LATENCY",
                        "GATEWAY_DOWNTIME",
                    ],
                    p=[0.30, 0.25, 0.15, 0.15, 0.10, 0.05],
                )
            refund_status = "NO_REFUND"
        else:
            payment_status = "SUCCESS"
            checkout_step = "PAYMENT_COMPLETED"
            failure_reason = "None"
            
            # Injected Issue 4: Fashion Refund Anomaly (18.5% refund rate vs ~3.5% baseline)
            if cat == "Fashion":
                refund_roll = rng.random()
                if refund_roll < 0.150:
                    refund_status = "FULL_REFUND"
                elif refund_roll < 0.185:
                    refund_status = "PARTIAL_REFUND"
                else:
                    refund_status = "NO_REFUND"
            else:
                refund_roll = rng.random()
                if refund_roll < 0.025:
                    refund_status = "FULL_REFUND"
                elif refund_roll < 0.040:
                    refund_status = "PARTIAL_REFUND"
                else:
                    refund_status = "NO_REFUND"

        records.append({
            "transaction_id": txn_id,
            "timestamp": txn_time.strftime("%Y-%m-%d %H:%M:%S"),
            "merchant_id": merchant_id,
            "customer_id": cust_id,
            "amount": amount,
            "payment_method": method,
            "payment_status": payment_status,
            "failure_reason": failure_reason,
            "device_type": device,
            "customer_type": cust_type,
            "product_category": cat,
            "refund_status": refund_status,
            "checkout_step_reached": checkout_step,
        })

    df = pd.DataFrame(records)

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)
        print(f"Generated {len(df)} transactions and saved to {output_path}")

    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic merchant payment data.")
    parser.add_argument("--records", type=int, default=15000, help="Number of records to generate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument(
        "--output",
        type=str,
        default="data/processed/merchant_transactions.csv",
        help="Path to save CSV output",
    )

    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    target_out = root / args.output

    df_gen = generate_synthetic_transactions(
        num_records=args.records,
        seed=args.seed,
        output_path=target_out,
    )

    # Print summary statistics
    print("\n--- Dataset Generation Summary ---")
    print(f"Total Transactions: {len(df_gen):,}")
    print(f"Time Range: {df_gen['timestamp'].min()} to {df_gen['timestamp'].max()}")
    print(f"Status Breakdown:\n{df_gen['payment_status'].value_counts(normalize=True).round(4) * 100}")
    print(f"\nPayment Method Distribution:\n{df_gen['payment_method'].value_counts()}")
    print(f"\nTop Failure Reasons:\n{df_gen[df_gen['failure_reason'] != 'None']['failure_reason'].value_counts().head(5)}")
    print(f"\nRefund Distribution in Fashion vs Overall:")
    print(f"Fashion: {(df_gen[df_gen['product_category'] == 'Fashion']['refund_status'] != 'NO_REFUND').mean() * 100:.1f}%")
    print(f"Overall: {(df_gen['refund_status'] != 'NO_REFUND').mean() * 100:.1f}%")
    total_rev = df_gen[df_gen['payment_status'] == 'SUCCESS']['amount'].sum()
    failed_rev = df_gen[df_gen['payment_status'] == 'FAILED']['amount'].sum()
    print(f"\nRealized Revenue: INR {total_rev:,.2f}")
    print(f"Failed Payment Volume: INR {failed_rev:,.2f}")
