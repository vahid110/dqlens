-- DQLens Demo: E-commerce schema with intentional data quality issues
-- Each issue is commented so you can see what DQLens should find.

-- ============================================================
-- SCHEMA
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

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id),
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    total_amount NUMERIC(10,2),
    email VARCHAR(255),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE order_items (
    id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES orders(id),
    product_id INTEGER REFERENCES products(id),
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(10,2) NOT NULL
);

-- A table with no data (DQLens should flag this)
CREATE TABLE audit_log (
    id SERIAL PRIMARY KEY,
    action VARCHAR(100),
    user_id INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

-- A stale table (last data from weeks ago)
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
        WHEN i % 5 = 0 THEN NULL  -- ISSUE: 20% null phones
        WHEN i % 7 = 0 THEN 'not-a-phone'  -- ISSUE: some invalid phone formats
        ELSE '+1-555-' || LPAD(i::text, 4, '0')
    END,
    NOW() - (random() * interval '365 days')
FROM generate_series(1, 500) AS i;

-- Products: 50 products, some with negative prices (ISSUE)
INSERT INTO products (name, price, category, created_at)
SELECT
    'Product ' || i,
    CASE
        WHEN i = 13 THEN -9.99   -- ISSUE: negative price
        WHEN i = 27 THEN -0.01   -- ISSUE: negative price
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

-- Orders: 2000 orders
-- ISSUE: some orders reference customer_id that will be deleted (orphans)
-- ISSUE: some orders have NULL email (drift scenario)
-- ISSUE: some orders have negative total_amount
INSERT INTO orders (customer_id, status, total_amount, email, created_at)
SELECT
    CASE
        WHEN i % 100 = 0 THEN 9999  -- ISSUE: references non-existent customer
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
        WHEN i % 50 = 0 THEN -10.00  -- ISSUE: negative amount
        ELSE round((random() * 500 + 5)::numeric, 2)
    END,
    CASE
        WHEN i > 1800 THEN NULL  -- ISSUE: last 10% of orders have NULL email
        ELSE 'user' || ((i % 500) + 1) || '@example.com'
    END,
    NOW() - (random() * interval '90 days')
FROM generate_series(1, 2000) AS i;

-- Order items: ~4000 items
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT
    (i % 2000) + 1,
    CASE
        WHEN i % 200 = 0 THEN 9999  -- ISSUE: references non-existent product
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

-- audit_log: intentionally left EMPTY (DQLens should flag this)

-- Run ANALYZE so pg_stats is populated
ANALYZE;
