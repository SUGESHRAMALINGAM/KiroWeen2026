"""Generate the retail sample CSVs (deterministic, with planted defects). Run once; output is committed."""
import csv
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "core" / "domain_packs" / "retail"
random.seed(42)


def write(name, header, rows):
    with open(OUT / name, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


customers = [(i, f"Customer {i}", f"c{i}@example.com" if i % 17 else "", random.choice(["North", "South", "East", "West"]))
             for i in range(1, 101)]
write("customers.csv", ["customer_id", "name", "email", "region"], customers)

products = [(i, f"Product {i}", random.choice(["Toys", "Books", "Food", "Tools"]), round(random.uniform(2, 90), 2))
            for i in range(1, 31)]
write("products.csv", ["product_id", "name", "category", "price"], products)

orders = []
for i in range(1, 301):
    qty = random.randint(1, 5)
    cust = random.randint(1, 100)
    status = random.choice(["NEW", "SHIPPED", "DONE"])
    orders.append([i, cust, random.randint(1, 30), qty, f"2026-01-{random.randint(1, 28):02d}", status])
orders[4][1] = ""          # null customer
orders[9][3] = 5000        # outlier quantity
orders[19][1] = 999        # orphan customer
orders[29][3] = -3         # negative quantity
orders.append(list(orders[49]))   # duplicate row
write("orders.csv", ["order_id", "customer_id", "product_id", "quantity", "order_date", "status"], orders)


# ---- outbreak demo: same schema, about a quarter of the orders are infected ----
OUT2 = OUT.parent / "retail_outbreak"
OUT2.mkdir(exist_ok=True)
random.seed(7)
rows = []
for i in range(1, 301):
    rows.append([i, random.randint(1, 100), random.randint(1, 30), random.randint(1, 5),
                 f"2026-02-{random.randint(1, 28):02d}", random.choice(["NEW", "SHIPPED", "DONE"])])
for i in range(0, 15):
    rows[10 + i * 9][1] = ""                       # missing customer
for i in range(0, 20):
    rows[5 + i * 11][1] = 900 + i                  # orphan customers
for i, v in enumerate([5000, 7500, -4, -9, 9000, 6000]):
    rows[3 + i * 40][3] = v                        # quantity outliers
for i in range(0, 15):
    d = rows[1 + i * 17][4].split("-")
    rows[1 + i * 17][4] = f"{d[2]}/{d[1]}/{d[0]}"   # mixed date format
rows += [list(rows[k]) for k in range(20, 45)]      # 25 duplicate rows
with open(OUT2 / "orders.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["order_id", "customer_id", "product_id", "quantity", "order_date", "status"])
    w.writerows(rows)
