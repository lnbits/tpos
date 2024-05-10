async def m001_initial(db):
    """
    Initial tposs table.
    """
    await db.execute(
        """
        CREATE TABLE tpos.tposs (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            name TEXT NOT NULL,
            currency TEXT NOT NULL
        );
    """
    )


async def m002_addtip_wallet(db):
    """
    Add tips to tposs table
    """
    await db.execute(
        """
        ALTER TABLE tpos.tposs ADD tip_wallet TEXT NULL;
    """
    )


async def m003_addtip_options(db):
    """
    Add tips to tposs table
    """
    await db.execute(
        """
        ALTER TABLE tpos.tposs ADD tip_options TEXT NULL;
    """
    )


async def m004_addwithdrawlimit(db):
    rows = [list(row) for row in await db.fetchall("SELECT * FROM tpos.tposs")]
    await db.execute(
        """
        CREATE TABLE tpos.pos (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            name TEXT NOT NULL,
            currency TEXT NOT NULL,
            tip_wallet TEXT NULL,
            tip_options TEXT NULL,
            withdrawlimit INTEGER DEFAULT 0,
            withdrawpin INTEGER DEFAULT 878787,
            withdrawamt INTEGER DEFAULT 0,
            withdrawtime INTEGER NOT NULL DEFAULT 0,
            withdrawbtwn INTEGER NOT NULL DEFAULT 10
        );
    """
    )
    for row in rows:
        await db.execute(
            """
            INSERT INTO tpos.pos (
                id,
                wallet,
                name,
                currency,
                tip_wallet,
                tip_options
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (row[0], row[1], row[2], row[3], row[4], row[5]),
        )
    await db.execute("DROP TABLE tpos.tposs")


async def m005_initial(db):
    """
    Initial withdraws table.
    """
    await db.execute(
        """
        CREATE TABLE tpos.withdraws (
            id TEXT PRIMARY KEY,
            tpos_id TEXT NOT NULL,
            amount int,
            claimed BOOLEAN DEFAULT false
        );
    """
    )


async def m006_items(db):
    """
    Add items to tpos table for storing various items (JSON format)
    See `Item` class in models.
    """
    await db.execute(
        """
        ALTER TABLE tpos.pos ADD items TEXT DEFAULT '[]';
        """
    )


async def m007_atm_premium(db):
    """
    Add a premium % to ATM withdraws
    """
    await db.execute("ALTER TABLE tpos.pos ADD COLUMN withdrawpremium FLOAT;")


async def m008_atm_time_option_and_pin_toggle(db):
    """
    Add a time mins/sec and pin toggle
    """
    await db.execute(
        "ALTER TABLE tpos.pos ADD COLUMN withdrawtimeopt TEXT DEFAULT 'mins';"
    )
    await db.execute(
        "ALTER TABLE tpos.pos ADD COLUMN withdrawpindisabled BOOL NOT NULL DEFAULT false;"
    )


async def m009_tax_inclusive(db):
    """
    Add tax_inclusive column
    """
    await db.execute(
        "ALTER TABLE tpos.pos ADD COLUMN tax_inclusive BOOL NOT NULL DEFAULT true;"
    )
    await db.execute("ALTER TABLE tpos.pos ADD COLUMN tax_default FLOAT;")
