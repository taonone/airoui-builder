-- Drop tables if they exist (Clean Slate)
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS order_items;

-- 1. Users Table
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    nickname TEXT,
    role TEXT DEFAULT 'user', -- 'admin' or 'user'
    points INTEGER DEFAULT 0, -- Points (initially 0)
    coupons INTEGER DEFAULT 10, -- 10 Free Purchase Coupons on Signup
    avatar TEXT DEFAULT '😊',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast login lookups
CREATE INDEX idx_users_email ON users(email);

-- 2. Products Table (Software)
CREATE TABLE products (
    id TEXT PRIMARY KEY, -- We use string IDs (e.g., 'prod_1234')
    user_id INTEGER, -- Creator (Admin)
    name TEXT NOT NULL,
    price INTEGER DEFAULT 0,
    category_id TEXT,
    description TEXT,
    source_code TEXT, -- HTML content
    version TEXT DEFAULT '1.0.0',
    details JSON, -- Store extra fields (tags, features, etc.) as JSON string
    stats JSON, -- { views: 0, downloads: 0 }
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Orders Table
CREATE TABLE orders (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    total_amount INTEGER NOT NULL,
    status TEXT DEFAULT 'completed',
    payment_method TEXT DEFAULT 'points',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- 4. Order Items Table
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    product_name TEXT,
    price INTEGER,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);
