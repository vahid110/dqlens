-- DQLens Demo: E-commerce schema with intentional data quality issues
-- Uses deferred FK checks and post-insert deletes to create realistic problems.

-- ============================================================
-- SCHEMA (FKs without enforcement for demo purposes)
-- ============================================================

CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    phone VARCHAR(50),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    price NUMERIC(10,2) NOT NULL,
    category VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Orders: FK to customers WITHOUT enforcement (common in data warehouses)
CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER,  -- no FK constraint (simulates warehouse pattern)
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    total_amount NUMERIC(10,2),
    email VARCHAR(255),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- But we tell DQLens about the relationship via a "soft FK" comment
-- DQLens discovers FKs from information_schema, so let's add a real
-- FK on order_items to demonstrate integrity checking there.
CREATE TABLE order_items (
    id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES orders(id),
    product_id INTEGER,  -- no FK constraint (simulates warehouse pattern)
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(10,2) NOT NULL
);

-- Empty table (DQLens should flag this)
CREATE TABLE audit_log (
    id SERIAL PRIMARY KEY,
    action VARCHAR(100),
    user_id INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Stale table (last data from weeks ago)
CREATE TABLE daily_reports (
    id SERIAL PRIMARY KEY,
    report_date DATE NOT NULL,
    total_revenue NUMERIC(12,2),
    order_count INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- SEED DATA
-- ============================================================

-- Customers: 500 real customers
INSERT INTO customers (email, name, phone, created_at)
SELECT
    'user' || i || '@example.com',
    'Customer ' || i,
    CASE
        WHEN i % 5 = 0 THEN NULL                          -- 20% null phones
        WHEN i % 7 = 0 THEN 'not-a-phone-' || i           -- invalid phone format
        ELSE '+1-555-' || LPAD(i::text, 4, '0')
    END,
    NOW() - (random() * interval '365 days')
FROM generate_series(1, 500) AS i;

-- Products: 50 products, some with NEGATIVE prices
INSERT INTO products (name, price, category, created_at)
SELECT
    'Product ' || i,
    CASE
        WHEN i = 13 THEN -9.99                             -- negative price!
        WHEN i = 27 THEN -0.01                             -- negative price!
        ELSE round((random() * 200 + 1)::numeric, 2)
    END,
    CASE (i % 5)
        WHEN 0 THEN 'Electronics'
        WHEN 1 THEN 'Clothing'
        WHEN 2 THEN 'Books'
        WHEN 3 THEN 'Home'
        WHEN 4 THEN 'Sports'
    END,
    NOW() - (random() * interval '180 days')
FROM generate_series(1, 50) AS i;

-- Orders: 2000 orders with various issues
INSERT INTO orders (customer_id, status, total_amount, email, created_at)
SELECT
    CASE
        WHEN i % 100 = 0 THEN 9999                        -- orphaned customer_id!
        ELSE (i % 500) + 1
    END,
    CASE (i % 6)
        WHEN 0 THEN 'pending'
        WHEN 1 THEN 'confirmed'
        WHEN 2 THEN 'shipped'
        WHEN 3 THEN 'delivered'
        WHEN 4 THEN 'cancelled'
        WHEN 5 THEN 'returned'
    END,
    CASE
        WHEN i % 50 = 0 THEN -10.00                       -- negative amount!
        ELSE round((random() * 500 + 5)::numeric, 2)
    END,
    CASE
        WHEN i > 1800 THEN NULL                            -- last 10% null email
        ELSE 'user' || ((i % 500) + 1) || '@example.com'
    END,
    NOW() - (random() * interval '90 days')
FROM generate_series(1, 2000) AS i;

-- Order items: ~4000 items
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT
    (i % 2000) + 1,
    CASE
        WHEN i % 200 = 0 THEN 9999                        -- orphaned product_id!
        ELSE (i % 50) + 1
    END,
    (random() * 5 + 1)::integer,
    round((random() * 200 + 1)::numeric, 2)
FROM generate_series(1, 4000) AS i;

-- Daily reports: stale data (last entry 14 days ago)
INSERT INTO daily_reports (report_date, total_revenue, order_count, created_at)
SELECT
    (CURRENT_DATE - (i || ' days')::interval)::date,
    round((random() * 50000 + 10000)::numeric, 2),
    (random() * 200 + 50)::integer,
    NOW() - (i || ' days')::interval
FROM generate_series(14, 90) AS i;

-- audit_log: intentionally left EMPTY

-- Run ANALYZE so pg_stats is populated
ANALYZE;

-- Summary of planted issues:
-- 1. audit_log: empty table (0 rows)
-- 2. daily_reports: stale (last data 14 days ago)
-- 3. orders.email: 10% null (200 rows)
-- 4. orders.total_amount: some negative values
-- 5. orders.customer_id: 20 rows reference customer 9999 (doesn't exist)
-- 6. customers.phone: 20% null + some invalid format
-- 7. products.price: 2 negative prices
-- 8. order_items.product_id: 20 rows reference product 9999 (doesn't exist)
