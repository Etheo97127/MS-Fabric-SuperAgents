from datetime import date, datetime, timedelta
from decimal import Decimal
import random

from pyspark.sql import Row
from pyspark.sql import functions as spark_function


RANDOM_SEED = 20260915
WORKSPACE_NAME = "Retail Analytics Dev"
BRONZE_LAKEHOUSE = "lh_bronze_retail_raw"
SILVER_LAKEHOUSE = "lh_silver_retail_curated"

BRONZE_TABLE_PATH = f"abfss://{WORKSPACE_NAME}@onelake.dfs.fabric.microsoft.com/{BRONZE_LAKEHOUSE}.Lakehouse/Tables"
SILVER_TABLE_PATH = f"abfss://{WORKSPACE_NAME}@onelake.dfs.fabric.microsoft.com/{SILVER_LAKEHOUSE}.Lakehouse/Tables"

random_generator = random.Random(RANDOM_SEED)
generation_date = date(2026, 9, 15)
start_date = generation_date - timedelta(days=90)

customer_segments = ["Consumer", "Small Business", "Enterprise"]
cities = ["Singapore", "Kuala Lumpur", "Jakarta", "Bangkok", "Manila"]
countries = ["Singapore", "Malaysia", "Indonesia", "Thailand", "Philippines"]
channels = ["Store", "Web", "Mobile", "Marketplace"]
stores = [f"STORE{store_number:03d}" for store_number in range(1, 16)]
categories = ["Audio", "Computing", "Home Office", "Mobile", "Accessories"]
brands = ["Aster", "Northline", "Kairo", "Vela", "Omni"]


def write_delta(table_name, dataframe, layer_path):
    dataframe.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(f"{layer_path}/{table_name}")


def random_day():
    return start_date + timedelta(days=random_generator.randint(0, 90))


customers = []
for customer_number in range(1, 501):
    city_index = random_generator.randrange(len(cities))
    customers.append(
        Row(
            customer_id=f"CUST{customer_number:06d}",
            full_name=f"Customer {customer_number:06d}",
            email=f"customer{customer_number:06d}@example.invalid",
            customer_segment=random_generator.choice(customer_segments),
            city=cities[city_index],
            country=countries[city_index],
            signup_date=random_day().isoformat(),
            marketing_consent=random_generator.choice([True, True, True, False]),
            source_updated_at=datetime.combine(random_day(), datetime.min.time()).isoformat(),
        )
    )

products = []
for product_number in range(1, 121):
    category = random_generator.choice(categories)
    base_price = Decimal(random_generator.randint(1200, 180000)) / Decimal("100")
    products.append(
        Row(
            product_id=f"PROD{product_number:06d}",
            product_name=f"{category} Product {product_number:03d}",
            category=category,
            brand=random_generator.choice(brands),
            list_price=float(base_price),
            active_flag=random_generator.choice([True, True, True, True, False]),
            source_updated_at=datetime.combine(random_day(), datetime.min.time()).isoformat(),
        )
    )

orders = []
order_lines = []
for order_number in range(1, 2001):
    order_id = f"ORD{order_number:07d}"
    order_date = random_day()
    customer = random_generator.choice(customers)
    order_status = random_generator.choice(["Completed", "Completed", "Completed", "Completed", "Cancelled"])
    line_count = random_generator.randint(1, 3)

    orders.append(
        Row(
            order_id=order_id,
            customer_id=customer.customer_id,
            order_timestamp=datetime.combine(order_date, datetime.min.time()).isoformat(),
            channel=random_generator.choice(channels),
            store_id=random_generator.choice(stores),
            order_status=order_status,
            source_updated_at=datetime.combine(order_date, datetime.min.time()).isoformat(),
        )
    )

    for line_number in range(1, line_count + 1):
        product = random_generator.choice(products)
        quantity = random_generator.randint(1, 5)
        discount_rate = random_generator.choice([0.0, 0.0, 0.05, 0.10, 0.15])
        order_lines.append(
            Row(
                order_line_id=f"{order_id}-{line_number:02d}",
                order_id=order_id,
                product_id=product.product_id,
                quantity=quantity,
                unit_price=float(product.list_price),
                discount_rate=discount_rate,
                source_updated_at=datetime.combine(order_date, datetime.min.time()).isoformat(),
            )
        )

inventory_snapshots = []
for snapshot_number in range(1, 3001):
    snapshot_date = start_date + timedelta(days=random_generator.randint(0, 90))
    product = random_generator.choice(products)
    inventory_snapshots.append(
        Row(
            inventory_snapshot_id=f"INV{snapshot_number:07d}",
            product_id=product.product_id,
            store_id=random_generator.choice(stores),
            snapshot_date=snapshot_date.isoformat(),
            on_hand_quantity=random_generator.choice([0, 0, random_generator.randint(1, 500)]),
            source_updated_at=datetime.combine(snapshot_date, datetime.min.time()).isoformat(),
        )
    )

ingested_at = datetime.utcnow().isoformat()
source_system = "synthetic-retail-generator"
batch_id = f"synthetic-{generation_date.isoformat()}"

bronze_tables = {
    "raw_crm_customers": customers,
    "raw_product_catalog": products,
    "raw_pos_orders": orders,
    "raw_pos_order_lines": order_lines,
    "raw_inventory_snapshots": inventory_snapshots,
}

for table_name, table_rows in bronze_tables.items():
    bronze_dataframe = spark.createDataFrame(table_rows)
    bronze_dataframe = (
        bronze_dataframe.withColumn("_ingested_at", spark_function.lit(ingested_at))
        .withColumn("_source_system", spark_function.lit(source_system))
        .withColumn("_source_file", spark_function.lit(None).cast("string"))
        .withColumn("_batch_id", spark_function.lit(batch_id))
    )
    write_delta(table_name, bronze_dataframe, BRONZE_TABLE_PATH)

raw_customers = spark.read.format("delta").load(f"{BRONZE_TABLE_PATH}/raw_crm_customers")
raw_products = spark.read.format("delta").load(f"{BRONZE_TABLE_PATH}/raw_product_catalog")
raw_orders = spark.read.format("delta").load(f"{BRONZE_TABLE_PATH}/raw_pos_orders")
raw_order_lines = spark.read.format("delta").load(f"{BRONZE_TABLE_PATH}/raw_pos_order_lines")
raw_inventory = spark.read.format("delta").load(f"{BRONZE_TABLE_PATH}/raw_inventory_snapshots")

silver_customers = raw_customers.dropDuplicates(["customer_id"]).select(
    "customer_id",
    "full_name",
    spark_function.lower("email").alias("email"),
    "customer_segment",
    "city",
    "country",
    spark_function.to_date("signup_date").alias("signup_date"),
    "marketing_consent",
)

silver_products = raw_products.dropDuplicates(["product_id"]).filter("active_flag = true").select(
    "product_id",
    "product_name",
    "category",
    "brand",
    spark_function.col("list_price").cast("decimal(12,2)").alias("list_price"),
    "active_flag",
)

silver_orders = raw_orders.filter("order_status <> 'Cancelled'").select(
    "order_id",
    "customer_id",
    spark_function.to_timestamp("order_timestamp").alias("order_timestamp"),
    spark_function.to_date("order_timestamp").alias("order_date"),
    "channel",
    "store_id",
    "order_status",
)

silver_order_lines = raw_order_lines.filter("quantity > 0 and unit_price >= 0").select(
    "order_line_id",
    "order_id",
    "product_id",
    spark_function.col("quantity").cast("int").alias("quantity"),
    spark_function.col("unit_price").cast("decimal(12,2)").alias("unit_price"),
    spark_function.col("discount_rate").cast("decimal(5,2)").alias("discount_rate"),
)

silver_inventory = raw_inventory.select(
    "inventory_snapshot_id",
    "product_id",
    "store_id",
    spark_function.to_date("snapshot_date").alias("snapshot_date"),
    spark_function.col("on_hand_quantity").cast("int").alias("on_hand_quantity"),
)

write_delta("customers", silver_customers, SILVER_TABLE_PATH)
write_delta("products", silver_products, SILVER_TABLE_PATH)
write_delta("orders", silver_orders, SILVER_TABLE_PATH)
write_delta("order_lines", silver_order_lines, SILVER_TABLE_PATH)
write_delta("inventory_snapshots", silver_inventory, SILVER_TABLE_PATH)

fact_sales = (
    silver_order_lines.join(silver_orders, "order_id", "inner")
    .join(silver_customers.select("customer_id", "customer_segment", "city", "country"), "customer_id", "left")
    .join(silver_products.select("product_id", "category", "brand"), "product_id", "left")
    .withColumn("gross_sales_amount", spark_function.col("quantity") * spark_function.col("unit_price"))
    .withColumn("net_sales_amount", spark_function.col("gross_sales_amount") * (spark_function.lit(1) - spark_function.col("discount_rate")))
)

agg_sales_daily = fact_sales.groupBy("order_date", "store_id", "channel").agg(
    spark_function.sum("net_sales_amount").alias("net_sales_amount"),
    spark_function.countDistinct("order_id").alias("order_count"),
    spark_function.sum("quantity").alias("unit_count"),
)

write_delta("gold_fact_sales_stage", fact_sales, SILVER_TABLE_PATH)
write_delta("gold_agg_sales_daily_stage", agg_sales_daily, SILVER_TABLE_PATH)

print("Synthetic retail Bronze and Silver Delta tables created.")
print("Gold staging Delta tables created for loading into wh_gold_retail_marts.")
