"""
Generate sample data files for AI examples.

This script creates realistic sample datasets for testing the data analysis agent.
"""

import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path


def generate_sales_data(output_path: str, num_records: int = 500):
    """Generate sample sales data CSV."""
    products = [
        ("Laptop Pro", 1299.99, 950.00),
        ("Wireless Mouse", 29.99, 12.00),
        ("USB-C Hub", 49.99, 22.00),
        ("Mechanical Keyboard", 149.99, 75.00),
        ("HD Webcam", 89.99, 45.00),
        ("Noise-Canceling Headphones", 299.99, 150.00),
        ("Portable SSD 1TB", 129.99, 80.00),
        ("Monitor 27inch", 399.99, 250.00),
        ("Desk Lamp LED", 39.99, 18.00),
        ("Ergonomic Chair", 449.99, 280.00),
    ]

    regions = ["North America", "Europe", "Asia", "South America", "Australia"]
    sales_channels = ["Online", "Retail", "Enterprise", "Partner"]

    # Generate dates over the past year
    start_date = datetime.now() - timedelta(days=365)

    data = []
    for _ in range(num_records):
        product_name, price, cost = random.choice(products)
        quantity = random.randint(1, 50)
        date = start_date + timedelta(days=random.randint(0, 365))

        # Add some seasonality (higher sales in Q4)
        if date.month in [11, 12]:
            quantity = int(quantity * 1.5)

        revenue = round(price * quantity, 2)
        total_cost = round(cost * quantity, 2)
        profit = round(revenue - total_cost, 2)
        profit_margin = round((profit / revenue) * 100, 2) if revenue > 0 else 0

        data.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "product": product_name,
                "region": random.choice(regions),
                "sales_channel": random.choice(sales_channels),
                "quantity": quantity,
                "unit_price": price,
                "revenue": revenue,
                "cost": total_cost,
                "profit": profit,
                "profit_margin": profit_margin,
            },
        )

    # Sort by date
    data.sort(key=lambda x: str(x["date"]))

    # Write CSV
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)

    print(f"Generated {num_records} sales records in {output_path}")
    return data


def generate_customer_data(output_path: str, num_records: int = 200):
    """Generate sample customer data JSON."""
    first_names = [
        "Alice",
        "Bob",
        "Charlie",
        "Diana",
        "Ethan",
        "Fiona",
        "George",
        "Hannah",
        "Ian",
        "Julia",
    ]
    last_names = [
        "Smith",
        "Johnson",
        "Williams",
        "Brown",
        "Jones",
        "Garcia",
        "Miller",
        "Davis",
        "Rodriguez",
        "Martinez",
    ]
    industries = ["Technology", "Healthcare", "Finance", "Retail", "Manufacturing", "Education"]
    company_sizes = ["Small (1-50)", "Medium (51-500)", "Large (501-5000)", "Enterprise (5000+)"]

    data = []
    for i in range(1, num_records + 1):
        first_name = random.choice(first_names)
        last_name = random.choice(last_names)
        signup_date = datetime.now() - timedelta(days=random.randint(0, 730))
        last_purchase_date = signup_date + timedelta(days=random.randint(0, 365))

        data.append(
            {
                "customer_id": f"CUST{i:04d}",
                "name": f"{first_name} {last_name}",
                "email": f"{first_name.lower()}.{last_name.lower()}@example.com",
                "company": f"{random.choice(['Tech', 'Global', 'Prime', 'Digital'])} {random.choice(['Solutions', 'Systems', 'Corp', 'Industries'])}",
                "industry": random.choice(industries),
                "company_size": random.choice(company_sizes),
                "signup_date": signup_date.strftime("%Y-%m-%d"),
                "last_purchase_date": last_purchase_date.strftime("%Y-%m-%d"),
                "total_purchases": random.randint(1, 50),
                "lifetime_value": round(random.uniform(100, 50000), 2),
                "is_active": random.choice([True, True, True, False]),  # 75% active
            },
        )

    # Write JSON
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Generated {num_records} customer records in {output_path}")
    return data


if __name__ == "__main__":
    # Generate sample datasets
    print("Generating sample data files...\n")

    sales_data = generate_sales_data("examples/ai/sample_data/sales_data.csv", num_records=500)
    customer_data = generate_customer_data(
        "examples/ai/sample_data/customer_data.json",
        num_records=200,
    )

    print("\n" + "=" * 60)
    print("Sample data generated successfully!")
    print("=" * 60)
    print("\nFiles created:")
    print("  - examples/ai/sample_data/sales_data.csv (500 records)")
    print("  - examples/ai/sample_data/customer_data.json (200 records)")
    print("\nYou can now run the data analysis agent:")
    print(
        '  flux workflow run data_analysis_agent_ollama \'{"file_path": "examples/ai/sample_data/sales_data.csv", "question": "What are the top products?"}\'',
    )
