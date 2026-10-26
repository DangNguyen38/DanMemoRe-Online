CREATE TABLE IF NOT EXISTS accounts (
                    uid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
                    uuid TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password TEXT NOT NULL,
                    migration_code TEXT UNIQUE, migration_salt TEXT, migration_password TEXT,
                    generation INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    hash TEXT PRIMARY KEY, uid INTEGER NOT NULL REFERENCES accounts(uid),
                    expires INTEGER NOT NULL, generation INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS transfers (
                    hash TEXT PRIMARY KEY, uid INTEGER NOT NULL REFERENCES accounts(uid),
                    expires INTEGER NOT NULL, generation INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    bucket TEXT PRIMARY KEY, start INTEGER NOT NULL, count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS devices (
                    uuid TEXT PRIMARY KEY REFERENCES accounts(uuid),
                    device_id TEXT NOT NULL, public_key TEXT NOT NULL,
                    generation INTEGER NOT NULL, updated INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS signed_nonces (
                    scope TEXT NOT NULL, nonce TEXT NOT NULL, expires INTEGER NOT NULL,
                    PRIMARY KEY(scope,nonce));
